"""Transactional repository for inventory, lifecycle, matches and delivery."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
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
        authoritative: bool | None = None,
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
                session.add(ProfileMatchRow(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)

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
                row = NotificationOutboxRow(**item.model_dump())
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
