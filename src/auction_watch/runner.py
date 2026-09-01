"""The durable, profile-driven execution boundary for Auction Watch."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from auction_watch.core.identity import decode_opportunity_key
from auction_watch.core.matching import match_lot
from auction_watch.core.models import AuctionGroup, AuctionLot, MatchResult
from auction_watch.persistence.contracts import (
    CoverageReceipt,
    GroupRecord,
    LotRecord,
    ProfileMatchRecord,
    RunProfileRecord,
    RunRecord,
    SourceRecord,
    SourceRunRecord,
)
from auction_watch.persistence.database import Database
from auction_watch.persistence.operational_repository import (
    OperationalRepository,
    RunLeaseBusyError,
)
from auction_watch.persistence.repository import ProfileRepository, StoredProfile
from auction_watch.sources import DEFAULT_SOURCE_REGISTRY, SourceRegistry
from auction_watch.sources.contracts import SourceScanResult
from auction_watch.sources.transport import HttpxTransport, Transport

logger = logging.getLogger(__name__)
LEASE_TTL = timedelta(minutes=5)
MAX_PARALLEL_SOURCES = 5
SCHEDULE_GRACE = timedelta(minutes=15)


def _sanitize_error(value: str) -> str:
    return re.sub(r"https?://\S+", "<url>", " ".join(value.split()))[:300]


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    snapshot_id: str | None
    content_hash: str | None
    errors: tuple[str, ...] = ()


def due_profiles(
    profiles: Sequence[StoredProfile],
    last_successful_by_profile: Mapping[str, datetime],
    now: datetime,
) -> tuple[str, ...]:
    """Return uncovered profiles only inside a bounded schedule window."""

    current = now.astimezone(UTC)
    due: list[str] = []
    for stored in sorted(profiles, key=lambda item: item.profile.id):
        profile = stored.profile
        if not profile.enabled or not profile.schedule.enabled or not profile.schedule.times:
            continue
        zone = ZoneInfo(profile.schedule.timezone)
        local_now = current.astimezone(zone)
        candidates = []
        for raw_time in profile.schedule.times:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
            candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= local_now:
                candidates.append(candidate.astimezone(UTC))
        if not candidates:
            continue
        latest_slot = max(candidates)
        if current >= latest_slot + SCHEDULE_GRACE:
            continue
        last_success = last_successful_by_profile.get(profile.id)
        if (
            last_success is None
            or last_success.astimezone(UTC) < latest_slot - SCHEDULE_GRACE
        ):
            due.append(profile.id)
    return tuple(due)


class AuctionRunEngine:
    """Coordinate one durable run without leaking orchestration into adapters."""

    def __init__(
        self,
        database: Database,
        *,
        profile_repository: ProfileRepository | None = None,
        operational_repository: OperationalRepository | None = None,
        source_registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY,
        transport_factory: Callable[[], Transport] = HttpxTransport,
        now: Callable[[], datetime] | None = None,
        lease_ttl: timedelta = LEASE_TTL,
    ) -> None:
        self.profiles = profile_repository or ProfileRepository(database)
        self.operational = operational_repository or OperationalRepository(database)
        self.sources = source_registry
        self.transport_factory = transport_factory
        self.now = now or (lambda: datetime.now(UTC))
        self.lease_ttl = lease_ttl

    def run(
        self,
        profile_id: str | Sequence[str],
        *,
        request_id: str | None = None,
        trigger: str = "manual",
    ) -> RunOutcome:
        profile_ids = (profile_id,) if isinstance(profile_id, str) else tuple(profile_id)
        run_id = request_id or str(uuid4())
        existing = self.operational.get_run(run_id)
        if existing is not None and existing.status in {"completed", "partial", "failed"}:
            return self._outcome(existing)

        acquired_at = self.now().astimezone(UTC)
        expires_at = acquired_at + self.lease_ttl
        previous_owner = self.operational.acquire_run_lease(
            run_id, acquired_at=acquired_at, expires_at=expires_at
        )
        if previous_owner:
            abandoned = self.operational.get_run(previous_owner)
            if abandoned is not None and abandoned.status == "running":
                self.operational.update_run(
                    abandoned.model_copy(
                        update={
                            "status": "failed",
                            "finished_at": acquired_at,
                            "error": "run lease expired",
                        }
                    )
                )

        created = False
        try:
            stored_profiles = self._load_profiles(profile_ids)
            source_ids = tuple(
                sorted(
                    {
                        source_id
                        for stored in stored_profiles
                        for source_id in stored.profile.source_ids
                    }
                )
            )
            queued = RunRecord(
                run_id=run_id,
                status="queued",
                started_at=acquired_at,
                trigger=trigger,
                selected_sources=source_ids,
            )
            if self.operational.get_run(run_id) is None:
                self.operational.create_run(queued)
                created = True
            else:
                existing = self.operational.get_run(run_id)
                if existing is not None and existing.status in {"completed", "partial", "failed"}:
                    return self._outcome(existing)
            running = queued.model_copy(update={"status": "running"})
            self.operational.update_run(running)
            for position, stored in enumerate(stored_profiles):
                self.operational.record_run_profile(
                    RunProfileRecord(
                        run_id=run_id,
                        profile_id=stored.profile.id,
                        revision=stored.revision,
                        position=position,
                    )
                )
            outcome = self._execute(running, stored_profiles, source_ids)
            return outcome
        except RunLeaseBusyError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}"
            logger.error(
                "auction_run_failed",
                extra={"run_id": run_id, "error": _sanitize_error(error)},
            )
            failed = self.operational.get_run(run_id)
            if failed is not None and (created or failed.status in {"queued", "running"}):
                failed = failed.model_copy(
                    update={"status": "failed", "finished_at": self.now(), "error": error}
                )
                self.operational.update_run(failed)
                return self._outcome(failed, errors=(error,))
            raise
        finally:
            self.operational.release_run_lease(run_id)

    def _load_profiles(self, profile_ids: tuple[str, ...]) -> list[StoredProfile]:
        stored = {item.profile.id: item for item in self.profiles.list() if item.profile.enabled}
        missing = sorted(set(profile_ids) - stored.keys())
        if missing:
            raise ValueError("unknown or disabled profile")
        if not profile_ids:
            raise ValueError("at least one profile is required")
        return [stored[profile_id] for profile_id in sorted(set(profile_ids))]

    def _execute(
        self,
        run: RunRecord,
        stored_profiles: list[StoredProfile],
        source_ids: tuple[str, ...],
    ) -> RunOutcome:
        results: dict[str, SourceScanResult] = {}
        try:
            specs = self.sources.select(source_ids)
            with ThreadPoolExecutor(max_workers=min(len(specs), MAX_PARALLEL_SOURCES)) as executor:
                futures = {
                    spec.source_id: executor.submit(self._scan_source, run.run_id, spec.source_id)
                    for spec in specs
                }
                for spec in specs:
                    scanned = futures[spec.source_id].result()
                    contract_error = self._source_contract_error(spec.source_id, scanned)
                    if contract_error is not None:
                        scanned = SourceScanResult(
                            source_id=spec.source_id,
                            label=spec.label,
                            discovery_status="failed",
                            inventory_authoritative=False,
                            errors=(f"source contract violation ({contract_error})",),
                        )
                    results[spec.source_id] = scanned
                    self._persist_source(
                        run.run_id, spec.source_id, spec.label, results[spec.source_id]
                    )
                    logger.info(
                        "auction_source_finished",
                        extra={"run_id": run.run_id, "source_id": spec.source_id},
                    )

            lots = self.operational.active_lots(source_ids)
            for stored in stored_profiles:
                expected: set[tuple[str, str, str]] = set()
                for lot in lots:
                    result = match_lot(stored.profile, self._domain_lot(lot))
                    if not result.matched:
                        continue
                    expected.add((lot.source_id, lot.auction_id, lot.lot_id))
                    self.operational.record_match(self._match_record(result, run.run_id))
                self.operational.deactivate_missing_matches(run.run_id, stored.profile.id, expected)
            persisted_matches = self.operational.active_matches(
                tuple(profile.profile.id for profile in stored_profiles)
            )
            lot_by_key = {
                (lot.source_id, lot.auction_id, lot.lot_id): lot for lot in lots
            }
            match_payloads: dict[str, list[dict[str, object]]] = {
                stored.profile.id: [] for stored in stored_profiles
            }
            for match in persisted_matches:
                lot_candidate = lot_by_key.get((match.source_id, match.auction_id, match.lot_id))
                if lot_candidate is not None:
                    match_payloads[match.profile_id].append(
                        self._match_payload(match, lot_candidate)
                    )
            for values in match_payloads.values():
                values.sort(key=lambda item: str(item["opportunity_key"]))

            status = self._run_status(tuple(results.values()))
            finished = self.now().astimezone(UTC)
            run_errors = self._errors(results)
            completed = run.model_copy(
                update={
                    "status": status,
                    "finished_at": finished,
                    "error": "; ".join(run_errors)[:1000] or None,
                }
            )
            if status == "failed":
                self.operational.update_run(completed)
                return RunOutcome(run.run_id, status, None, None, run_errors)
            payload = self._snapshot_payload(
                completed, stored_profiles, results, match_payloads, source_ids
            )
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            self.operational.update_run(completed)
            snapshot_id = f"{run.run_id}:snapshot"
            self.operational.record_snapshot(
                snapshot_id,
                run.run_id,
                content_hash,
                status,
                payload,
                published_at=finished,
            )
            return RunOutcome(run.run_id, status, snapshot_id, content_hash, run_errors)
        except Exception:
            raise

    def _scan_source(self, run_id: str, source_id: str) -> SourceScanResult:
        """Scan one source in isolation; persistence remains single-threaded."""

        spec = self.sources.select((source_id,))[0]
        transport = self.transport_factory()
        logger.info("auction_source_started", extra={"run_id": run_id, "source_id": source_id})
        try:
            source = self.sources.build(transport, source_ids=(source_id,))[0]
            return source.scan()
        except Exception as exc:
            return SourceScanResult(
                source_id=spec.source_id,
                label=spec.label,
                discovery_status="failed",
                errors=(f"source scan failed ({type(exc).__name__})",),
            )
        finally:
            close = getattr(transport, "close", None)
            if callable(close):
                close()

    def _persist_source(
        self, run_id: str, source_id: str, label: str, result: SourceScanResult
    ) -> None:
        started = self.now().astimezone(UTC)
        source_status = {
            "complete": "succeeded",
            "partial": "degraded",
            "failed": "failed",
        }[result.discovery_status]
        self.operational.upsert_source(SourceRecord(source_id=source_id, label=label))
        self.operational.upsert_source_run(
            SourceRunRecord(
                run_id=run_id,
                source_id=source_id,
                status=source_status,
                discovered_count=len(result.groups) + len(result.skipped_groups),
                processed_count=len(result.receipts) + len(result.skipped_groups),
                failed_count=sum(receipt.status == "failed" for receipt in result.receipts),
                inventory_authoritative=result.inventory_authoritative,
                started_at=started,
                finished_at=self.now(),
                error="; ".join(_sanitize_error(error) for error in result.errors)[:1000] or None,
            )
        )
        for group in result.groups:
            self.operational.upsert_group(self._group_record(group))
        lots_by_group: dict[str, list[LotRecord]] = {}
        for lot in result.lots:
            lots_by_group.setdefault(lot.auction_id, []).append(self._lot_record(lot))
        for receipt in result.receipts:
            persisted = CoverageReceipt(
                run_id=run_id,
                source_id=source_id,
                **receipt.model_dump(exclude={"run_id"}),
            )
            self.operational.record_receipt(persisted)
            self.operational.reconcile_group(
                run_id,
                source_id,
                receipt.group_id,
                lots_by_group.get(receipt.group_id, []),
            )
        self.operational.reconcile_omitted_groups(run_id, source_id)

    @staticmethod
    def _source_contract_error(
        expected_source_id: str, result: SourceScanResult
    ) -> str | None:
        if result.source_id != expected_source_id:
            return "unexpected source identity"
        group_ids = [group.auction_id for group in result.groups]
        if len(group_ids) != len(set(group_ids)):
            return "duplicate group identity"
        receipt_ids = [receipt.group_id for receipt in result.receipts]
        if len(receipt_ids) != len(set(receipt_ids)):
            return "duplicate group receipt"
        skipped_ids = [group.group_id for group in result.skipped_groups]
        if len(skipped_ids) != len(set(skipped_ids)):
            return "duplicate skipped group"
        identities = [
            (lot.source_id, lot.auction_id, lot.lot_id) for lot in result.lots
        ]
        if len(identities) != len(set(identities)):
            return "duplicate lot identity"
        if any(group.source_id != expected_source_id for group in result.groups):
            return "group belongs to another source"
        if any(lot.source_id != expected_source_id for lot in result.lots):
            return "lot belongs to another source"
        group_id_set = set(group_ids)
        if group_id_set & set(skipped_ids):
            return "group cannot be both scanned and skipped"
        # A receipt is required for every discovered group.  A failed receipt is
        # still coverage: it tells reconciliation to retain that group's prior
        # inventory.  Excluding it here wrongly escalates one failed group into
        # a source-wide contract failure and hides healthy-source results.
        if group_id_set != set(receipt_ids):
            return "groups and coverage receipts do not match"
        if any(lot.auction_id not in group_id_set for lot in result.lots):
            return "lot belongs to an undiscovered group"
        lot_counts: dict[str, int] = {group_id: 0 for group_id in group_ids}
        for lot in result.lots:
            lot_counts[lot.auction_id] += 1
        if any(
            receipt.status != "failed"
            and receipt.lot_count != lot_counts.get(receipt.group_id, 0)
            for receipt in result.receipts
        ):
            return "coverage receipt lot count does not match inventory"
        if any(
            receipt.inventory_authoritative and receipt.status != "complete"
            for receipt in result.receipts
        ):
            return "non-complete receipt claims authority"
        if result.inventory_authoritative and (
            result.discovery_status != "complete"
            or bool(result.errors)
            or any(
                not receipt.inventory_authoritative
                for receipt in result.receipts
                if receipt.status != "failed"
            )
        ):
            return "source authority conflicts with coverage"
        return None

    @staticmethod
    def _group_record(group: AuctionGroup) -> GroupRecord:
        values = group.model_dump()
        values["group_id"] = values.pop("auction_id")
        return GroupRecord(**values)

    @staticmethod
    def _lot_record(lot: AuctionLot) -> LotRecord:
        return LotRecord(**lot.model_dump())

    @staticmethod
    def _domain_lot(lot: LotRecord) -> AuctionLot:
        return AuctionLot(**lot.model_dump())

    @staticmethod
    def _match_record(result: MatchResult, run_id: str) -> ProfileMatchRecord:
        now = datetime.now(UTC)
        return ProfileMatchRecord(
            profile_id=result.profile_id,
            source_id=decode_opportunity_key(result.opportunity_key)[0],
            auction_id=decode_opportunity_key(result.opportunity_key)[1],
            lot_id=decode_opportunity_key(result.opportunity_key)[2],
            score=result.score,
            matched_terms=result.matched_terms,
            matched_fields=result.matched_fields,
            first_seen_at=now,
            last_seen_at=now,
            active=True,
            first_match_at=now,
            last_match_at=now,
            confirmed_match_run_id=run_id,
        )

    @staticmethod
    def _match_payload(result: ProfileMatchRecord, lot: LotRecord) -> dict[str, object]:
        return {
            "opportunity_key": lot.opportunity_key,
            "score": result.score,
            "matched_terms": list(result.matched_terms),
            "matched_fields": {key: list(value) for key, value in result.matched_fields.items()},
            "lot": lot.model_dump(mode="json"),
        }

    def _snapshot_payload(
        self,
        run: RunRecord,
        profiles: list[StoredProfile],
        results: Mapping[str, SourceScanResult],
        matches: Mapping[str, list[dict[str, object]]],
        source_ids: tuple[str, ...],
    ) -> dict[str, object]:
        lifecycles = self.operational.lifecycles(source_ids)
        states = self.operational.user_states(tuple(profile.profile.id for profile in profiles))
        unified: dict[str, list[str]] = {}
        for profile_id, profile_matches in matches.items():
            for item in profile_matches:
                key = str(item["opportunity_key"])
                unified.setdefault(key, []).append(profile_id)
        return {
            "run": {
                "run_id": run.run_id,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "selected_sources": list(source_ids),
            },
            "sources": [
                {
                    "source_id": source_id,
                    "status": results[source_id].discovery_status,
                    "inventory_authoritative": results[source_id].inventory_authoritative,
                    "groups": [
                        receipt.model_dump(mode="json")
                        for receipt in results[source_id].receipts
                    ],
                    "skipped_groups": [
                        group.model_dump(mode="json")
                        for group in results[source_id].skipped_groups
                    ],
                    "errors": [_sanitize_error(error) for error in results[source_id].errors],
                    "warnings": [
                        _sanitize_error(warning) for warning in results[source_id].warnings
                    ],
                }
                for source_id in source_ids
            ],
            "profiles": [
                {
                    "profile_id": stored.profile.id,
                    "revision": stored.revision,
                    "matches": matches.get(stored.profile.id, []),
                }
                for stored in profiles
            ],
            "unified": [
                {"opportunity_key": key, "profiles": sorted(profile_ids)}
                for key, profile_ids in sorted(unified.items())
            ],
            "opportunities": [item.model_dump(mode="json") for item in lifecycles],
            "user_states": [item.model_dump(mode="json") for item in states],
        }

    @staticmethod
    def _run_status(results: Sequence[SourceScanResult]) -> str:
        if not results or all(result.discovery_status == "failed" for result in results):
            return "failed"
        if any(result.discovery_status != "complete" or result.errors for result in results):
            return "partial"
        return "completed"

    @staticmethod
    def _errors(results: Mapping[str, SourceScanResult]) -> tuple[str, ...]:
        return tuple(
            _sanitize_error(error)
            for source_id in sorted(results)
            for error in results[source_id].errors
        )

    def _outcome(self, run: RunRecord, errors: tuple[str, ...] = ()) -> RunOutcome:
        snapshot = self.operational.snapshot_for_run(run.run_id)
        return RunOutcome(
            run_id=run.run_id,
            status=run.status,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            content_hash=snapshot.content_hash if snapshot else None,
            errors=errors or ((run.error,) if run.error else ()),
        )


__all__ = ["AuctionRunEngine", "RunOutcome", "due_profiles"]
