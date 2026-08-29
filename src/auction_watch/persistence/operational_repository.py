"""Transactional repository for inventory, lifecycle, matches and delivery."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auction_watch.core.identity import encode_opportunity_key
from auction_watch.persistence.contracts import (
    CoverageReceipt,
    GroupRecord,
    LotRecord,
    NotificationOutboxRecord,
    OpportunityLifecycle,
    ProfileMatchRecord,
    RunProfileRecord,
    RunRecord,
    SourceRecord,
    SourceRunRecord,
    UserOpportunityState,
)
from auction_watch.persistence.database import Database
from auction_watch.persistence.models import (
    AuctionGroupRow as GroupRow,
)
from auction_watch.persistence.models import (
    AuctionLotRow,
    AuctionSnapshotRow,
    CoverageReceiptRow,
    NotificationOutboxRow,
    OpportunityRow,
    ProfileMatchRow,
    RunLeaseRow,
    RunProfileRow,
    RunRow,
    RunSourceRow,
    SourceRow,
    UserOpportunityStateRow,
)


class OperationalPersistenceError(RuntimeError):
    """Base class for operational persistence errors."""


class UserStateRevisionConflict(OperationalPersistenceError):
    """Raised when an optimistic user-state update is stale."""


class ReconciliationReceiptError(OperationalPersistenceError):
    """Raised when a group cannot be reconciled from a matching coverage receipt."""


class RunLeaseBusyError(OperationalPersistenceError):
    """Raised when another process owns the durable run lease."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OperationalRepository:
    """Keep source observations and derived lifecycle state in one transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert_source(self, source: SourceRecord) -> None:
        now = _utc_now()
        with self._database.sessions.begin() as session:
            row = session.get(SourceRow, source.source_id)
            if row is None:
                session.add(
                    SourceRow(
                        source_id=source.source_id,
                        label=source.label,
                        enabled=source.enabled,
                        metadata_json=dict(source.metadata),
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.label = source.label
                row.enabled = source.enabled
                row.metadata_json = dict(source.metadata)
                row.updated_at = now

    def upsert_group(self, group: GroupRecord) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(GroupRow, (group.source_id, group.group_id))
            values = {
                "title": group.title,
                "url": group.url,
                "category": group.category,
                "active": group.active,
                "closing_at": group.closing_at,
                "observed_at": group.observed_at,
            }
            if row is None:
                session.add(
                    GroupRow(
                        source_id=group.source_id,
                        group_id=group.group_id,
                        **values,
                    )
                )
            else:
                for key, value in values.items():
                    setattr(row, key, value)

    def upsert_lot(self, lot: LotRecord) -> None:
        with self._database.sessions.begin() as session:
            self._upsert_lot(session, lot)

    @staticmethod
    def _upsert_lot(session: Session, lot: LotRecord) -> None:
        row = session.get(AuctionLotRow, (lot.source_id, lot.auction_id, lot.lot_id))
        values = {
            "title": lot.title,
            "description": lot.description,
            "category": lot.category,
            "price_value": lot.price_value,
            "price_currency": lot.price_currency,
            "price_label": lot.price_label,
            "closing_at": lot.closing_at,
            "lot_url": lot.lot_url,
            "auction_url": lot.auction_url,
            "image_url": lot.image_url,
            "active": lot.active,
            "observed_at": lot.observed_at,
        }
        if row is None:
            session.add(
                AuctionLotRow(
                    source_id=lot.source_id,
                    auction_id=lot.auction_id,
                    lot_id=lot.lot_id,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(row, key, value)

    def create_run(self, run: RunRecord) -> None:
        with self._database.sessions.begin() as session:
            session.add(
                RunRow(
                    run_id=run.run_id,
                    status=run.status,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    error=run.error,
                    trigger=run.trigger,
                    selected_sources=list(run.selected_sources),
                )
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._database.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            return RunRecord(
                run_id=row.run_id,
                status=row.status,
                started_at=_as_utc(row.started_at),
                finished_at=_as_utc(row.finished_at) if row.finished_at else None,
                error=row.error,
                trigger=row.trigger,
                selected_sources=tuple(row.selected_sources),
            )

    def run_profile_ids(self, run_id: str) -> tuple[str, ...]:
        """Return the profiles bound to a run in their persisted order."""

        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(RunProfileRow)
                .where(RunProfileRow.run_id == run_id)
                .order_by(RunProfileRow.position, RunProfileRow.profile_id)
            ).all()
            return tuple(row.profile_id for row in rows)

    def update_run(self, run: RunRecord) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(RunRow, run.run_id)
            if row is None:
                raise OperationalPersistenceError(f"run not found: {run.run_id}")
            row.status = run.status
            row.started_at = run.started_at
            row.finished_at = run.finished_at
            row.error = run.error
            row.trigger = run.trigger
            row.selected_sources = list(run.selected_sources)

    def record_run_profile(self, record: RunProfileRecord) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(RunProfileRow, (record.run_id, record.profile_id))
            if row is None:
                session.add(RunProfileRow(**record.model_dump()))
            else:
                row.revision = record.revision
                row.position = record.position

    def acquire_run_lease(
        self, run_id: str, *, acquired_at: datetime, expires_at: datetime
    ) -> str | None:
        """Acquire the singleton lease atomically, replacing only an expired owner."""

        with self._database.engine.connect() as connection:
            connection.info["begin_immediate"] = True
            transaction = connection.begin()
            try:
                row = connection.execute(
                    select(RunLeaseRow.run_id, RunLeaseRow.expires_at).where(
                        RunLeaseRow.lease_name == "run-engine"
                    )
                ).one_or_none()
                if (
                    row is not None
                    and _as_utc(row.expires_at) > acquired_at
                    and row.run_id != run_id
                ):
                    raise RunLeaseBusyError(row.run_id)
                previous = row.run_id if row is not None else None
                values = {
                    "lease_name": "run-engine",
                    "run_id": run_id,
                    "acquired_at": acquired_at,
                    "expires_at": expires_at,
                }
                if row is None:
                    connection.execute(insert(RunLeaseRow).values(**values))
                else:
                    connection.execute(
                        update(RunLeaseRow)
                        .where(RunLeaseRow.lease_name == "run-engine")
                        .values(**values)
                    )
                transaction.commit()
                return previous if previous != run_id else None
            except Exception:
                transaction.rollback()
                raise

    def release_run_lease(self, run_id: str) -> None:
        with self._database.sessions.begin() as session:
            session.execute(
                delete(RunLeaseRow).where(
                    RunLeaseRow.lease_name == "run-engine", RunLeaseRow.run_id == run_id
                )
            )

    def upsert_source_run(self, result: SourceRunRecord) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(RunSourceRow, (result.run_id, result.source_id))
            values = result.model_dump()
            if row is None:
                session.add(RunSourceRow(**values))
            else:
                for key, value in values.items():
                    if key not in {"run_id", "source_id"}:
                        setattr(row, key, value)

    def record_receipt(self, receipt: CoverageReceipt) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(
                CoverageReceiptRow,
                (receipt.run_id, receipt.source_id, receipt.group_id),
            )
            values = receipt.model_dump()
            if row is None:
                session.add(CoverageReceiptRow(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)

    def reconcile_group(
        self,
        run_id: str,
        source_id: str,
        group_id: str,
        lots: list[LotRecord],
        *,
        observed_at: datetime | None = None,
    ) -> list[OpportunityLifecycle]:
        """Upsert one group using only its persisted coverage receipt as authority."""

        observed = observed_at or _utc_now()
        identities = [(lot.source_id, lot.auction_id, lot.lot_id) for lot in lots]
        if any(lot.source_id != source_id or lot.auction_id != group_id for lot in lots):
            raise ValueError("all lots must belong to the reconciled source and group")
        if len(identities) != len(set(identities)):
            raise ValueError("reconciliation lots must not contain duplicate identities")
        with self._database.sessions.begin() as session:
            receipt = session.get(CoverageReceiptRow, (run_id, source_id, group_id))
            if receipt is None:
                raise ReconciliationReceiptError(
                    f"missing coverage receipt for {run_id}/{source_id}/{group_id}"
                )
            receipt_authoritative = (
                receipt.status == "complete"
                and receipt.inventory_authoritative
                and receipt.lot_count == len(lots)
            )
            lot_ids = {lot.lot_id for lot in lots}
            for lot in lots:
                self._upsert_lot(session, lot)
                self._touch_lifecycle(session, lot, run_id, observed)
            if receipt_authoritative:
                rows = session.scalars(
                    select(AuctionLotRow).where(
                        AuctionLotRow.source_id == source_id,
                        AuctionLotRow.auction_id == group_id,
                    )
                ).all()
                for row in rows:
                    if row.lot_id not in lot_ids:
                        self._remove_lifecycle(session, row, run_id, observed)
            return self._lifecycle_for_group(session, source_id, group_id)

    def reconcile_omitted_groups(self, run_id: str, source_id: str) -> None:
        """Remove omitted groups only from persisted authoritative source evidence."""

        with self._database.sessions.begin() as session:
            source_run = session.get(RunSourceRow, (run_id, source_id))
            if (
                source_run is None
                or source_run.status != "succeeded"
                or not source_run.inventory_authoritative
            ):
                return
            receipt_groups = set(
                session.scalars(
                    select(CoverageReceiptRow.group_id).where(
                        CoverageReceiptRow.run_id == run_id,
                        CoverageReceiptRow.source_id == source_id,
                    )
                ).all()
            )
            observed = _utc_now()
            groups = session.scalars(
                select(GroupRow).where(GroupRow.source_id == source_id)
            ).all()
            for group in groups:
                if group.group_id in receipt_groups:
                    continue
                old_lots = session.scalars(
                    select(AuctionLotRow).where(
                        AuctionLotRow.source_id == source_id,
                        AuctionLotRow.auction_id == group.group_id,
                    )
                ).all()
                for old_lot in old_lots:
                    self._remove_lifecycle(session, old_lot, run_id, observed)

    @staticmethod
    def _touch_lifecycle(session: Session, lot: LotRecord, run_id: str, observed: datetime) -> None:
        row = session.get(OpportunityRow, (lot.source_id, lot.auction_id, lot.lot_id))
        if row is None:
            session.add(
                OpportunityRow(
                    source_id=lot.source_id,
                    auction_id=lot.auction_id,
                    lot_id=lot.lot_id,
                    first_seen_at=observed,
                    last_seen_at=observed,
                    seen_count=1,
                    active=True,
                    last_present_run_id=run_id,
                )
            )
            return
        already_counted = row.last_present_run_id == run_id or row.last_absence_run_id == run_id
        row.last_seen_at = observed
        if not already_counted:
            row.seen_count += 1
        row.active = True
        row.removed_at = None
        row.last_present_run_id = run_id

    @staticmethod
    def _remove_lifecycle(
        session: Session, lot: AuctionLotRow, run_id: str, observed: datetime
    ) -> None:
        row = session.get(OpportunityRow, (lot.source_id, lot.auction_id, lot.lot_id))
        if row is not None and row.active and row.last_absence_run_id != run_id:
            row.active = False
            row.removed_at = observed
            row.last_absence_run_id = run_id

    @staticmethod
    def _lifecycle_for_group(
        session: Session, source_id: str, group_id: str
    ) -> list[OpportunityLifecycle]:
        rows = session.scalars(
            select(OpportunityRow)
            .where(
                OpportunityRow.source_id == source_id,
                OpportunityRow.auction_id == group_id,
            )
            .order_by(OpportunityRow.lot_id)
        ).all()
        return [
            OpportunityLifecycle(
                source_id=row.source_id,
                auction_id=row.auction_id,
                lot_id=row.lot_id,
                first_seen_at=_as_utc(row.first_seen_at),
                last_seen_at=_as_utc(row.last_seen_at),
                seen_count=row.seen_count,
                active=row.active,
                removed_at=_as_utc(row.removed_at) if row.removed_at else None,
                last_present_run_id=row.last_present_run_id,
                last_absence_run_id=row.last_absence_run_id,
                opportunity_key=encode_opportunity_key(row.source_id, row.auction_id, row.lot_id),
            )
            for row in rows
        ]

    def record_match(self, match: ProfileMatchRecord) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(
                ProfileMatchRow,
                (match.profile_id, match.source_id, match.auction_id, match.lot_id),
            )
            values = match.model_dump()
            if row is None:
                if values["first_match_at"] is None:
                    values["first_match_at"] = match.last_seen_at
                if values["last_match_at"] is None:
                    values["last_match_at"] = match.last_seen_at
                session.add(ProfileMatchRow(**values))
            else:
                values["first_seen_at"] = row.first_seen_at
                values["first_match_at"] = row.first_match_at or row.first_seen_at
                values["last_match_at"] = match.last_seen_at
                values["active"] = True
                values["confirmed_absence_run_id"] = None
                for key, value in values.items():
                    setattr(row, key, value)

    def deactivate_missing_matches(
        self, run_id: str, profile_id: str, expected_keys: set[tuple[str, str, str]]
    ) -> None:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(ProfileMatchRow).where(
                    ProfileMatchRow.profile_id == profile_id,
                    ProfileMatchRow.active.is_(True),
                )
            ).all()
            for row in rows:
                if (row.source_id, row.auction_id, row.lot_id) not in expected_keys:
                    row.active = False
                    row.confirmed_absence_run_id = run_id

    def active_matches(self, profile_ids: tuple[str, ...]) -> list[ProfileMatchRecord]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(ProfileMatchRow)
                .where(
                    ProfileMatchRow.profile_id.in_(profile_ids),
                    ProfileMatchRow.active.is_(True),
                )
                .order_by(
                    ProfileMatchRow.profile_id,
                    ProfileMatchRow.source_id,
                    ProfileMatchRow.auction_id,
                    ProfileMatchRow.lot_id,
                )
            ).all()
            return [
                ProfileMatchRecord(
                    profile_id=row.profile_id,
                    source_id=row.source_id,
                    auction_id=row.auction_id,
                    lot_id=row.lot_id,
                    score=row.score,
                    matched_terms=tuple(row.matched_terms),
                    matched_fields={
                        key: tuple(value) for key, value in row.matched_fields.items()
                    },
                    first_seen_at=_as_utc(row.first_seen_at),
                    last_seen_at=_as_utc(row.last_seen_at),
                    active=row.active,
                    first_match_at=_as_utc(row.first_match_at) if row.first_match_at else None,
                    last_match_at=_as_utc(row.last_match_at) if row.last_match_at else None,
                    confirmed_match_run_id=row.confirmed_match_run_id,
                    confirmed_absence_run_id=row.confirmed_absence_run_id,
                )
                for row in rows
            ]

    def active_lots(self, source_ids: tuple[str, ...]) -> list[LotRecord]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(AuctionLotRow)
                .join(
                    OpportunityRow,
                    (OpportunityRow.source_id == AuctionLotRow.source_id)
                    & (OpportunityRow.auction_id == AuctionLotRow.auction_id)
                    & (OpportunityRow.lot_id == AuctionLotRow.lot_id),
                )
                .where(
                    AuctionLotRow.source_id.in_(source_ids),
                    OpportunityRow.active.is_(True),
                )
                .order_by(AuctionLotRow.source_id, AuctionLotRow.auction_id, AuctionLotRow.lot_id)
            ).all()
            return [self._lot_record(row) for row in rows]

    def lifecycles(self, source_ids: tuple[str, ...]) -> list[OpportunityLifecycle]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(OpportunityRow)
                .where(OpportunityRow.source_id.in_(source_ids))
                .order_by(
                    OpportunityRow.source_id,
                    OpportunityRow.auction_id,
                    OpportunityRow.lot_id,
                )
            ).all()
            return [
                OpportunityLifecycle(
                    source_id=row.source_id,
                    auction_id=row.auction_id,
                    lot_id=row.lot_id,
                    first_seen_at=_as_utc(row.first_seen_at),
                    last_seen_at=_as_utc(row.last_seen_at),
                    seen_count=row.seen_count,
                    active=row.active,
                    removed_at=_as_utc(row.removed_at) if row.removed_at else None,
                    last_present_run_id=row.last_present_run_id,
                    last_absence_run_id=row.last_absence_run_id,
                    opportunity_key=encode_opportunity_key(
                        row.source_id, row.auction_id, row.lot_id
                    ),
                )
                for row in rows
            ]

    def user_states(self, profile_ids: tuple[str, ...]) -> list[UserOpportunityState]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(UserOpportunityStateRow)
                .where(UserOpportunityStateRow.profile_id.in_(profile_ids))
                .order_by(
                    UserOpportunityStateRow.profile_id,
                    UserOpportunityStateRow.source_id,
                    UserOpportunityStateRow.auction_id,
                    UserOpportunityStateRow.lot_id,
                )
            ).all()
            return [
                UserOpportunityState(
                    profile_id=row.profile_id,
                    source_id=row.source_id,
                    auction_id=row.auction_id,
                    lot_id=row.lot_id,
                    state=row.state,
                    version=row.version,
                    created_at=_as_utc(row.created_at),
                    updated_at=_as_utc(row.updated_at),
                )
                for row in rows
            ]

    def lot_exists(self, source_id: str, auction_id: str, lot_id: str) -> bool:
        """Return whether an opportunity identity is present in reconciled storage."""

        with self._database.sessions.begin() as session:
            return (
                session.get(AuctionLotRow, (source_id, auction_id, lot_id)) is not None
            )

    @staticmethod
    def _lot_record(row: AuctionLotRow) -> LotRecord:
        return LotRecord(
            source_id=row.source_id,
            auction_id=row.auction_id,
            lot_id=row.lot_id,
            title=row.title,
            description=row.description,
            category=row.category,
            price_value=row.price_value,
            price_currency=row.price_currency,
            price_label=row.price_label,
            closing_at=_as_utc(row.closing_at) if row.closing_at else None,
            lot_url=row.lot_url,
            auction_url=row.auction_url,
            image_url=row.image_url,
            active=row.active,
            observed_at=_as_utc(row.observed_at),
        )

    def set_user_state(
        self,
        state: UserOpportunityState,
        *,
        expected_version: int | None = None,
    ) -> UserOpportunityState:
        now = _utc_now()
        with self._database.sessions.begin() as session:
            row = session.get(
                UserOpportunityStateRow,
                (state.profile_id, state.source_id, state.auction_id, state.lot_id),
            )
            if row is None:
                if expected_version not in (None, 0):
                    raise UserStateRevisionConflict(state.lot_id)
                session.add(
                    UserOpportunityStateRow(
                        **state.model_dump(exclude={"created_at", "updated_at", "version"}),
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                return state.model_copy(update={"version": 1, "created_at": now, "updated_at": now})
            if expected_version is not None and row.version != expected_version:
                raise UserStateRevisionConflict(state.lot_id)
            row.state = state.state
            row.version += 1
            row.updated_at = now
            return state.model_copy(
                update={
                    "version": row.version,
                    "created_at": _as_utc(row.created_at),
                    "updated_at": now,
                }
            )

    def enqueue_notification(self, item: NotificationOutboxRecord) -> int:
        try:
            with self._database.sessions.begin() as session:
                existing = session.scalar(
                    select(NotificationOutboxRow).where(
                        NotificationOutboxRow.dedupe_key == item.dedupe_key
                    )
                )
                if existing is not None:
                    return int(existing.id)
                values = item.model_dump()
                values["payload_json"] = values.pop("payload")
                row = NotificationOutboxRow(**values)
                session.add(row)
                session.flush()
                return int(row.id)
        except IntegrityError:
            with self._database.sessions.begin() as session:
                existing = session.scalar(
                    select(NotificationOutboxRow).where(
                        NotificationOutboxRow.dedupe_key == item.dedupe_key
                    )
                )
                if existing is not None:
                    return int(existing.id)
            raise OperationalPersistenceError("notification enqueue failed") from None

    def record_snapshot(
        self,
        snapshot_id: str,
        run_id: str,
        content_hash: str,
        status: str,
        payload: dict[str, object],
        published_at: datetime | None = None,
    ) -> None:
        with self._database.sessions.begin() as session:
            session.add(
                AuctionSnapshotRow(
                    snapshot_id=snapshot_id,
                    run_id=run_id,
                    content_hash=content_hash,
                    status=status,
                    payload_json=payload,
                    published_at=published_at,
                )
            )

    def snapshot_for_run(self, run_id: str) -> AuctionSnapshotRow | None:
        with self._database.sessions.begin() as session:
            return session.scalar(
                select(AuctionSnapshotRow).where(AuctionSnapshotRow.run_id == run_id)
            )

    def latest_snapshot(self) -> AuctionSnapshotRow | None:
        with self._database.sessions.begin() as session:
            return session.scalar(
                select(AuctionSnapshotRow)
                .where(AuctionSnapshotRow.published_at.is_not(None))
                .order_by(AuctionSnapshotRow.published_at.desc())
                .limit(1)
            )
