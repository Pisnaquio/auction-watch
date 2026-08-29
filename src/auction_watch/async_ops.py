"""Durable run queue, worker coordination and notification delivery records."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from auction_watch.persistence.contracts import NotificationOutboxRecord, RunQueueRecord
from auction_watch.persistence.database import Database
from auction_watch.persistence.models import (
    NotificationOutboxRow,
    RunQueueRow,
    RunRow,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sanitize_error(value: str) -> str:
    return re.sub(r"https?://\S+", "<url>", " ".join(value.split()))[:300]


class RunQueueRepository:
    """Claim and update run jobs with SQLite transaction boundaries."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _record(row: RunQueueRow) -> RunQueueRecord:
        return RunQueueRecord(
            idempotency_key=row.idempotency_key,
            run_id=row.run_id,
            profile_id=row.profile_id,
            trigger=row.trigger,
            status=row.status,
            attempt=row.attempt,
            enqueued_at=_as_utc(row.enqueued_at),
            available_at=_as_utc(row.available_at),
            started_at=_as_utc(row.started_at) if row.started_at else None,
            finished_at=_as_utc(row.finished_at) if row.finished_at else None,
            error=row.error,
        )

    def get_by_key(self, idempotency_key: str) -> RunQueueRecord | None:
        with self._database.sessions.begin() as session:
            row = session.scalar(
                select(RunQueueRow).where(RunQueueRow.idempotency_key == idempotency_key)
            )
            return None if row is None else self._record(row)

    def get(self, run_id: str) -> RunQueueRecord | None:
        with self._database.sessions.begin() as session:
            row = session.scalar(select(RunQueueRow).where(RunQueueRow.run_id == run_id))
            return None if row is None else self._record(row)

    def enqueue(
        self,
        *,
        idempotency_key: str,
        profile_id: str,
        trigger: str,
        revision: int,
        now: datetime | None = None,
    ) -> tuple[RunQueueRecord, bool]:
        """Create a queued run and its run record atomically, or return the existing job."""

        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            existing = session.scalar(
                select(RunQueueRow).where(RunQueueRow.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return self._record(existing), False
            run_id = str(uuid4())
            try:
                with session.begin_nested():
                    session.add(
                        RunRow(
                            run_id=run_id,
                            status="queued",
                            started_at=timestamp,
                            finished_at=None,
                            error=None,
                            trigger=trigger,
                            selected_sources=[],
                        )
                    )
                    session.flush()
                    row = RunQueueRow(
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                        profile_id=profile_id,
                        trigger=trigger,
                        status="queued",
                        attempt=0,
                        enqueued_at=timestamp,
                        available_at=timestamp,
                        started_at=None,
                        finished_at=None,
                        error=None,
                    )
                    session.add(row)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(RunQueueRow).where(
                        RunQueueRow.idempotency_key == idempotency_key
                    )
                )
                if existing is None:
                    raise
                return self._record(existing), False
            # The revision is intentionally read by the worker from the profile row;
            # accepting it here documents the enqueue boundary without duplicating it.
            _ = revision
            return self._record(row), True

    def recover_interrupted(self, *, now: datetime | None = None) -> int:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            result = session.execute(
                update(RunQueueRow)
                .where(RunQueueRow.status == "running")
                .values(status="queued", available_at=timestamp, started_at=None)
            )
            return cast(CursorResult[tuple[object, ...]], result).rowcount

    def claim_next(self, *, now: datetime | None = None) -> RunQueueRecord | None:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            session.connection().info["begin_immediate"] = True
            row = session.scalar(
                select(RunQueueRow)
                .where(
                    RunQueueRow.status == "queued",
                    RunQueueRow.available_at <= timestamp,
                )
                .order_by(RunQueueRow.enqueued_at, RunQueueRow.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "running"
            row.attempt += 1
            row.started_at = timestamp
            session.flush()
            return self._record(row)

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> RunQueueRecord:
        timestamp = (finished_at or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            row = session.scalar(select(RunQueueRow).where(RunQueueRow.run_id == run_id))
            if row is None:
                raise ValueError(f"run queue item not found: {run_id}")
            row.status = status
            row.finished_at = timestamp
            row.error = _sanitize_error(error) if error else None
            session.flush()
            return self._record(row)

    def requeue(
        self,
        run_id: str,
        *,
        error: str,
        now: datetime | None = None,
        delay: timedelta = timedelta(seconds=5),
    ) -> RunQueueRecord:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            row = session.scalar(select(RunQueueRow).where(RunQueueRow.run_id == run_id))
            if row is None:
                raise ValueError(f"run queue item not found: {run_id}")
            row.status = "queued"
            row.available_at = timestamp + delay
            row.error = _sanitize_error(error)
            row.started_at = None
            session.flush()
            return self._record(row)

    def last_successful_by_profile(self) -> dict[str, datetime]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(RunQueueRow).where(
                    RunQueueRow.status == "completed",
                    RunQueueRow.finished_at.is_not(None),
                )
            ).all()
            result: dict[str, datetime] = {}
            for row in rows:
                finished = _as_utc(row.finished_at)  # type: ignore[arg-type]
                previous = result.get(row.profile_id)
                if previous is None or finished > previous:
                    result[row.profile_id] = finished
            return result

    def recent(self, profile_id: str, limit: int = 20) -> list[RunQueueRecord]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(RunQueueRow)
                .where(RunQueueRow.profile_id == profile_id)
                .order_by(RunQueueRow.enqueued_at.desc(), RunQueueRow.id.desc())
                .limit(limit)
            ).all()
            return [self._record(row) for row in rows]


class NotificationRepository:
    """Persist logical notifications and claim delivery attempts safely."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _record(row: NotificationOutboxRow) -> NotificationOutboxRecord:
        return NotificationOutboxRecord(
            dedupe_key=row.dedupe_key,
            channel=row.channel,
            profile_id=row.profile_id,
            run_id=row.run_id,
            snapshot_id=row.snapshot_id,
            status=row.status,
            attempts=row.attempts,
            last_error=row.last_error,
            next_attempt_at=_as_utc(row.next_attempt_at) if row.next_attempt_at else None,
            notification_type=row.notification_type,
            payload=row.payload_json,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def enqueue(self, item: NotificationOutboxRecord) -> tuple[NotificationOutboxRecord, bool]:
        try:
            with self._database.sessions.begin() as session:
                existing = session.scalar(
                    select(NotificationOutboxRow).where(
                        NotificationOutboxRow.dedupe_key == item.dedupe_key
                    )
                )
                if existing is not None:
                    return self._record(existing), False
                row = NotificationOutboxRow(
                    dedupe_key=item.dedupe_key,
                    channel=item.channel,
                    profile_id=item.profile_id,
                    run_id=item.run_id,
                    snapshot_id=item.snapshot_id,
                    status=item.status,
                    attempts=item.attempts,
                    last_error=item.last_error,
                    next_attempt_at=item.next_attempt_at,
                    notification_type=item.notification_type,
                    payload_json=item.payload,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                session.add(row)
                session.flush()
                return self._record(row), True
        except IntegrityError:
            with self._database.sessions.begin() as session:
                existing = session.scalar(
                    select(NotificationOutboxRow).where(
                        NotificationOutboxRow.dedupe_key == item.dedupe_key
                    )
                )
                if existing is None:
                    raise
                return self._record(existing), False

    def claim_due(
        self, *, now: datetime | None = None, max_attempts: int = 3
    ) -> NotificationOutboxRecord | None:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            session.connection().info["begin_immediate"] = True
            row = session.scalar(
                select(NotificationOutboxRow)
                .where(
                    NotificationOutboxRow.status.in_(["pending", "failed"]),
                    NotificationOutboxRow.attempts < max_attempts,
                    (NotificationOutboxRow.next_attempt_at.is_(None))
                    | (NotificationOutboxRow.next_attempt_at <= timestamp),
                )
                .order_by(NotificationOutboxRow.created_at, NotificationOutboxRow.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "sending"
            row.attempts += 1
            row.updated_at = timestamp
            session.flush()
            return self._record(row)

    def recover_inflight(self, *, now: datetime | None = None) -> int:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            result = session.execute(
                update(NotificationOutboxRow)
                .where(NotificationOutboxRow.status == "sending")
                .values(status="pending", updated_at=timestamp)
            )
            return cast(CursorResult[tuple[object, ...]], result).rowcount

    def mark_sent(self, dedupe_key: str, *, now: datetime | None = None) -> None:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            session.execute(
                update(NotificationOutboxRow)
                .where(NotificationOutboxRow.dedupe_key == dedupe_key)
                .values(status="sent", last_error=None, next_attempt_at=None, updated_at=timestamp)
            )

    def mark_failed(
        self,
        dedupe_key: str,
        error: str,
        *,
        now: datetime | None = None,
        max_attempts: int = 3,
        base_delay: timedelta = timedelta(seconds=5),
    ) -> None:
        timestamp = (now or _utc_now()).astimezone(UTC)
        with self._database.sessions.begin() as session:
            row = session.scalar(
                select(NotificationOutboxRow).where(
                    NotificationOutboxRow.dedupe_key == dedupe_key
                )
            )
            if row is None:
                raise ValueError(f"notification not found: {dedupe_key}")
            row.status = "failed"
            row.last_error = _sanitize_error(error)
            row.next_attempt_at = (
                timestamp + base_delay * (2 ** max(0, row.attempts - 1))
                if row.attempts < max_attempts
                else None
            )
            row.updated_at = timestamp

    def recent(self, profile_id: str, limit: int = 20) -> list[NotificationOutboxRecord]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(
                select(NotificationOutboxRow)
                .where(NotificationOutboxRow.profile_id == profile_id)
                .order_by(NotificationOutboxRow.created_at.desc(), NotificationOutboxRow.id.desc())
                .limit(limit)
            ).all()
            return [self._record(row) for row in rows]


__all__ = ["NotificationRepository", "RunQueueRepository"]
