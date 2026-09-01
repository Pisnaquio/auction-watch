from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from auction_watch.async_ops import NotificationRepository, RunQueueRepository
from auction_watch.core.identity import encode_opportunity_key
from auction_watch.core.models import SearchProfile
from auction_watch.notifications.sender import FakeNotificationSender, NotificationMessage
from auction_watch.notifications.service import NotificationPlanner
from auction_watch.persistence.contracts import NotificationOutboxRecord, RunRecord
from auction_watch.persistence.database import Database
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.operational_repository import OperationalRepository
from auction_watch.persistence.repository import ProfileRepository
from auction_watch.runner import RunOutcome
from auction_watch.scheduler import enqueue_due_profiles
from auction_watch.worker import NotificationDeliveryWorker, RunWorker

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def make_profile(profile_id: str = "libros", *, mode: str = "disabled") -> SearchProfile:
    return SearchProfile(
        id=profile_id,
        name="Libros",
        source_ids=["bavastro"],
        keywords_any=["libro"],
        notification_mode=mode,
        schedule={
            "enabled": True,
            "times": ["09:00"],
            "timezone": "America/Montevideo",
        },
    )


def database(tmp_path: Path) -> Database:
    db = Database.open(tmp_path)
    upgrade_head(tmp_path, db.engine)
    ProfileRepository(db).create(make_profile())
    return db


def add_run(database: Database, run_id: str) -> None:
    OperationalRepository(database).create_run(
        RunRecord(
            run_id=run_id,
            status="completed",
            started_at=NOW,
            finished_at=NOW,
            selected_sources=("bavastro",),
        )
    )


def test_enqueue_is_idempotent_and_recovery_is_exclusive(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        queue = RunQueueRepository(db)
        first, created = queue.enqueue(
            idempotency_key="manual-1", profile_id="libros", trigger="manual", revision=1, now=NOW
        )
        second, duplicate = queue.enqueue(
            idempotency_key="manual-1", profile_id="libros", trigger="manual", revision=1, now=NOW
        )
        assert created is True
        assert duplicate is False
        assert first.run_id == second.run_id

        claimed = queue.claim_next(now=NOW)
        assert claimed is not None
        assert RunQueueRepository(db).claim_next(now=NOW) is None
        assert queue.recover_interrupted(now=NOW + timedelta(minutes=1)) == 1
        recovered = queue.claim_next(now=NOW + timedelta(minutes=1))
        assert recovered is not None
        assert recovered.run_id == claimed.run_id
        assert recovered.attempt == 2
    finally:
        db.dispose()


def test_worker_processes_recovered_job_once(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        queue = RunQueueRepository(db)
        queued, _ = queue.enqueue(
            idempotency_key="manual-2", profile_id="libros", trigger="manual", revision=1, now=NOW
        )
        assert queue.claim_next(now=NOW) is not None

        class FakeEngine:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, profile_id: str, *, request_id: str, trigger: str) -> RunOutcome:
                self.calls += 1
                operational = OperationalRepository(db)
                run = operational.get_run(request_id)
                assert run is not None
                operational.update_run(
                    run.model_copy(update={"status": "completed", "finished_at": NOW})
                )
                return RunOutcome(request_id, "completed", None, None)

        fake = FakeEngine()
        worker = RunWorker(
            fake, ProfileRepository(db), OperationalRepository(db), queue, now=lambda: NOW
        )
        result = worker.run_once()
        assert result is not None
        assert fake.calls == 1
        assert queue.get(queued.run_id).status == "completed"
        assert worker.run_once() is None
    finally:
        db.dispose()


def test_scheduler_respects_pause_and_manual_success_covers_slot(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        profiles = ProfileRepository(db)
        queue = RunQueueRepository(db)
        assert enqueue_due_profiles(profiles, queue, now=NOW) == ("libros",)
        assert enqueue_due_profiles(profiles, queue, now=NOW) == ()
        job = queue.claim_next(now=NOW)
        assert job is not None
        queue.finish(job.run_id, status="partial", finished_at=NOW)
        assert enqueue_due_profiles(profiles, queue, now=NOW) == ()
        current = profiles.get("libros")
        assert current is not None
        profiles.replace(
            current.profile.model_copy(update={"enabled": False}),
            expected_revision=current.revision,
        )
        assert enqueue_due_profiles(profiles, queue, now=NOW + timedelta(days=1)) == ()
    finally:
        db.dispose()


def test_scheduler_honors_timezone_and_never_backfills_hours_late(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        profiles = ProfileRepository(db)
        queue = RunQueueRepository(db)
        utc = make_profile("utc-profile").model_copy(
            update={
                "schedule": make_profile("utc-profile").schedule.model_copy(
                    update={"timezone": "UTC"}
                )
            }
        )
        profiles.create(utc)

        assert enqueue_due_profiles(profiles, queue, now=NOW - timedelta(hours=3)) == (
            "utc-profile",
        )
        assert enqueue_due_profiles(profiles, queue, now=NOW - timedelta(minutes=1)) == ()
        assert enqueue_due_profiles(profiles, queue, now=NOW + timedelta(minutes=16)) == ()
    finally:
        db.dispose()


def test_fresh_manual_partial_run_covers_the_daily_slot(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        queue = RunQueueRepository(db)
        manual, created = queue.enqueue(
            idempotency_key="manual-covers-slot",
            profile_id="libros",
            trigger="manual",
            revision=1,
            now=NOW + timedelta(minutes=1),
        )
        assert created is True
        claimed = queue.claim_next(now=NOW + timedelta(minutes=1))
        assert claimed is not None and claimed.run_id == manual.run_id
        queue.finish(
            manual.run_id,
            status="partial",
            finished_at=NOW + timedelta(minutes=2),
        )
        assert enqueue_due_profiles(
            ProfileRepository(db), queue, now=NOW + timedelta(minutes=3)
        ) == ()
    finally:
        db.dispose()


def test_manual_run_just_before_schedule_also_covers_the_slot(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        queue = RunQueueRepository(db)
        manual, _ = queue.enqueue(
            idempotency_key="manual-before-slot",
            profile_id="libros",
            trigger="manual",
            revision=1,
            now=NOW - timedelta(minutes=5),
        )
        assert queue.claim_next(now=NOW - timedelta(minutes=5)) is not None
        queue.finish(
            manual.run_id,
            status="completed",
            finished_at=NOW - timedelta(minutes=1),
        )
        assert enqueue_due_profiles(ProfileRepository(db), queue, now=NOW) == ()
    finally:
        db.dispose()


def test_outbox_logical_once_retry_backoff_and_fake_sender(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        repository = NotificationRepository(db)
        item = NotificationOutboxRecord(
            dedupe_key="same-logical-mail",
            channel="smtp",
            profile_id="libros",
            payload={
                "subject": "Novedades",
                "body": "Hay una oportunidad",
            },
            created_at=NOW,
            updated_at=NOW,
        )
        first, created = repository.enqueue(item)
        second, duplicate = repository.enqueue(item)
        assert created is True and duplicate is False
        assert first.dedupe_key == second.dedupe_key

        sender = FakeNotificationSender()
        delivery = NotificationDeliveryWorker(repository, sender, now=lambda: NOW)
        assert delivery.run_once() is True
        assert delivery.run_once() is False
        assert len(sender.messages) == 1
        assert repository.recent("libros")[0].status == "sent"

        retry_item = item.model_copy(update={"dedupe_key": "retry-mail"})
        repository.enqueue(retry_item)
        sender.failures_remaining = 1
        delivery = NotificationDeliveryWorker(repository, sender, now=lambda: NOW)
        assert delivery.run_once() is True
        failed = next(row for row in repository.recent("libros") if row.dedupe_key == "retry-mail")
        assert failed.status == "failed"
        assert failed.next_attempt_at == NOW + timedelta(seconds=5)
        later = NotificationDeliveryWorker(
            repository, sender, now=lambda: NOW + timedelta(seconds=6)
        )
        assert later.run_once() is True
        retry = next(row for row in repository.recent("libros") if row.dedupe_key == "retry-mail")
        assert retry.status == "sent"
    finally:
        db.dispose()


def test_planner_notifies_only_new_matches_or_failures(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        add_run(db, "run-new")
        profiles = ProfileRepository(db)
        stored = profiles.get("libros")
        assert stored is not None
        stored = profiles.replace(
            stored.profile.model_copy(update={"notification_mode": "matches"}),
            expected_revision=stored.revision,
        )
        repository = NotificationRepository(db)
        planner = NotificationPlanner(repository, enabled=True)
        match = {
            "opportunity_key": encode_opportunity_key("bavastro", "auction", "lot"),
            "score": 4,
            "matched_terms": ["libro"],
            "lot": {"title": "Libro", "lot_url": "https://example.test/lot"},
        }
        snapshot = SimpleNamespace(
            snapshot_id="snapshot-new",
            payload_json={"profiles": [{"profile_id": "libros", "matches": [match]}]},
        )
        OperationalRepository(db).record_snapshot(
            "snapshot-new",
            "run-new",
            "hash",
            "completed",
            snapshot.payload_json,
            published_at=NOW,
        )
        outcome = RunOutcome("run-new", "completed", "snapshot-new", "hash")
        notification = planner.plan(stored, outcome, snapshot, None)
        assert notification is not None
        assert notification.notification_type == "matches"
        assert planner.plan(stored, outcome, snapshot, None) is not None
        assert len(repository.recent("libros")) == 1

        add_run(db, "run-unchanged")
        unchanged = SimpleNamespace(
            snapshot_id="snapshot-unchanged",
            payload_json={"profiles": [{"profile_id": "libros", "matches": [match]}]},
            status="completed",
        )
        OperationalRepository(db).record_snapshot(
            "snapshot-unchanged",
            "run-unchanged",
            "hash-unchanged",
            "completed",
            unchanged.payload_json,
            published_at=NOW + timedelta(seconds=1),
        )
        assert planner.plan(
            stored,
            RunOutcome("run-unchanged", "completed", "snapshot-unchanged", "hash-unchanged"),
            unchanged,
            unchanged,
        ) is None

        add_run(db, "run-failed")
        failure_profile = ProfileRepository(db).replace(
            stored.profile.model_copy(update={"notification_mode": "matches_or_failure"}),
            expected_revision=stored.revision,
        )
        failure = planner.plan(
            failure_profile,
            RunOutcome("run-failed", "failed", None, None, ("HTTP 503 https://secret.test/token",)),
            None,
            None,
        )
        assert failure is not None
        assert "https://secret.test" not in str(failure.payload)

        sender = FakeNotificationSender()
        delivery = NotificationDeliveryWorker(repository, sender, now=lambda: NOW)
        while delivery.run_once():
            pass
        assert len(sender.messages) == 2
        assert {item.status for item in repository.recent("libros")} == {"sent"}
    finally:
        db.dispose()


def test_fake_sender_contract_never_needs_network() -> None:
    sender = FakeNotificationSender()
    sender.send(NotificationMessage("subject", "body"))
    assert len(sender.messages) == 1
