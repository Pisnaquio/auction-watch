from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auction_watch.core.models import AuctionGroup, AuctionLot, SearchProfile, SearchSchedule
from auction_watch.persistence import (
    Database,
    OperationalRepository,
    ProfileRepository,
    RunLeaseBusyError,
    RunRecord,
    UserOpportunityState,
    upgrade_head,
)
from auction_watch.runner import AuctionRunEngine, due_profiles
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.registry import SourceRegistry, SourceSpec

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


@dataclass
class SourceState:
    result: SourceScanResult
    calls: int = 0
    failure: Exception | None = None


class FakeSource(BaseAuctionSource):
    source_id = "fake"
    label = "Fake"
    discovery_url = "https://fake.test/discovery"

    def __init__(self, transport, state: SourceState) -> None:
        super().__init__(transport)
        self.state = state

    def scan(self) -> SourceScanResult:
        self.state.calls += 1
        if self.state.failure is not None:
            raise self.state.failure
        return self.state.result


def group() -> AuctionGroup:
    return AuctionGroup(
        source_id="fake",
        auction_id="auction:1",
        title="Auction",
        url="https://fake.test/auction/1",
        observed_at=NOW,
        active=True,
    )


def lot(lot_id: str = "lot:1") -> AuctionLot:
    return AuctionLot(
        source_id="fake",
        auction_id="auction:1",
        lot_id=lot_id,
        title="Console retro",
        description="A useful console",
        lot_url=f"https://fake.test/lot/{lot_id}",
        auction_url="https://fake.test/auction/1",
        observed_at=NOW,
        active=True,
    )


def complete_result(*lots: AuctionLot) -> SourceScanResult:
    return SourceScanResult(
        source_id="fake",
        label="Fake",
        groups=(group(),),
        lots=tuple(lots),
        discovery_status="complete",
        inventory_authoritative=True,
        receipts=(
            GroupReceipt(
                group_id="auction:1",
                status="complete",
                inventory_authoritative=True,
                lot_count=len(lots),
                error_count=0,
                started_at=NOW,
                finished_at=NOW,
            ),
        ),
    )


def profile(profile_id: str = "profile-a") -> SearchProfile:
    return SearchProfile(
        id=profile_id,
        name=profile_id,
        source_ids=["fake"],
        keywords_any=["console"],
    )


def engine(
    tmp_path: Path, state: SourceState, *profiles: SearchProfile
) -> tuple[Database, AuctionRunEngine]:
    database = Database.open(tmp_path)
    upgrade_head(tmp_path, database.engine)
    profiles_repo = ProfileRepository(database)
    for item in profiles:
        profiles_repo.create(item)
    registry = SourceRegistry(
        (SourceSpec("fake", "Fake", lambda transport: FakeSource(transport, state)),)
    )
    return database, AuctionRunEngine(
        database,
        source_registry=registry,
        transport_factory=lambda: object(),
        now=lambda: NOW,
    )


def test_run_persists_matches_snapshot_and_is_idempotent(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        first = runner.run("profile-a", request_id="request-1")
        second = runner.run("profile-a", request_id="request-1")
        assert first.status == second.status == "completed"
        assert first.content_hash == second.content_hash
        assert state.calls == 1
        assert first.snapshot_id is not None
        assert database.check_ready()
        repository = OperationalRepository(database)
        assert len(repository.active_lots(("fake",))) == 1
        assert len(repository.active_matches(("profile-a",))) == 1
        assert repository.latest_snapshot() is not None
    finally:
        database.dispose()


def test_shared_source_is_scanned_once_for_multiple_profiles(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile("profile-a"), profile("profile-b"))
    try:
        result = runner.run(("profile-a", "profile-b"), request_id="request-2")
        assert result.status == "completed"
        assert state.calls == 1
        assert OperationalRepository(database).active_matches(("profile-a", "profile-b"))
    finally:
        database.dispose()


def test_partial_source_preserves_previous_inventory_and_degrades_run(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        assert runner.run("profile-a", request_id="complete").status == "completed"
        state.result = SourceScanResult(
            source_id="fake",
            label="Fake",
            discovery_status="partial",
            inventory_authoritative=False,
            errors=("timeout",),
        )
        result = runner.run("profile-a", request_id="partial")
        assert result.status == "partial"
        assert result.snapshot_id is not None
        lifecycle = OperationalRepository(database).lifecycles(("fake",))
        assert lifecycle[0].active is True
    finally:
        database.dispose()


def test_complete_empty_discovery_closes_omitted_group_but_partial_does_not(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        runner.run("profile-a", request_id="present")
        state.result = SourceScanResult(
            source_id="fake",
            label="Fake",
            discovery_status="complete",
            inventory_authoritative=True,
        )
        result = runner.run("profile-a", request_id="empty")
        assert result.status == "completed"
        assert OperationalRepository(database).lifecycles(("fake",))[0].active is False
    finally:
        database.dispose()


def test_failed_run_does_not_replace_last_snapshot(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        first = runner.run("profile-a", request_id="good")
        state.result = SourceScanResult(
            source_id="fake", label="Fake", discovery_status="failed", errors=("HTTP 503",)
        )
        second = runner.run("profile-a", request_id="bad")
        assert second.status == "failed"
        assert second.snapshot_id is None
        assert OperationalRepository(database).latest_snapshot().snapshot_id == first.snapshot_id
    finally:
        database.dispose()


def test_user_state_is_preserved_and_manual_success_covers_next_schedule_slot(
    tmp_path: Path,
) -> None:
    scheduled = profile().model_copy(
        update={
            "schedule": SearchSchedule(enabled=True, times=["12:00"], timezone="UTC"),
        }
    )
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, scheduled)
    try:
        runner.run("profile-a", request_id="scheduled-manual")
        repository = OperationalRepository(database)
        repository.set_user_state(
            UserOpportunityState(
                profile_id="profile-a",
                source_id="fake",
                auction_id="auction:1",
                lot_id="lot:1",
                state="following",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        stored = ProfileRepository(database).list()
        assert due_profiles(stored, {"profile-a": NOW}, NOW) == ()
        assert repository.user_states(("profile-a",))[0].state == "following"
    finally:
        database.dispose()


def test_profile_revision_recalculates_and_deactivates_historical_match(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        runner.run("profile-a", request_id="revision-before")
        repository = ProfileRepository(database)
        current = repository.get("profile-a")
        assert current is not None
        repository.replace(
            profile("profile-a").model_copy(update={"keywords_any": ["not-present"]}),
            expected_revision=current.revision,
        )
        result = runner.run("profile-a", request_id="revision-after")
        assert result.status == "completed"
        assert OperationalRepository(database).active_matches(("profile-a",)) == []
        assert result.snapshot_id is not None
    finally:
        database.dispose()


def test_due_profiles_excludes_paused_profiles() -> None:
    paused = profile().model_copy(
        update={"schedule": SearchSchedule(enabled=True, times=["11:00"], timezone="UTC")}
    )
    paused = paused.model_copy(update={"enabled": False})
    assert due_profiles(
        [type("Stored", (), {"profile": paused})()], {}, NOW
    ) == ()


def test_missing_receipt_is_fail_closed(tmp_path: Path) -> None:
    database = Database.open(tmp_path)
    upgrade_head(tmp_path, database.engine)
    repository = OperationalRepository(database)
    try:
        with pytest.raises(Exception, match="missing coverage receipt"):
            repository.reconcile_group("run", "fake", "auction:1", [])
    finally:
        database.dispose()


def test_concurrent_runs_have_one_lease_winner_and_no_duplicate_scan(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        repository = OperationalRepository(database)
        repository.acquire_run_lease(
            "concurrent",
            acquired_at=NOW,
            expires_at=datetime(2026, 8, 29, 12, 5, tzinfo=UTC),
        )
        with pytest.raises(RunLeaseBusyError, match="concurrent"):
            runner.run("profile-a", request_id="other")
        repository.release_run_lease("concurrent")
        assert runner.run("profile-a", request_id="concurrent").status == "completed"
        assert state.calls == 1
    finally:
        database.dispose()


def test_expired_lease_is_recovered_and_old_run_is_closed(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()))
    database, runner = engine(tmp_path, state, profile())
    try:
        repository = OperationalRepository(database)
        repository.create_run(RunRecord(run_id="abandoned", status="running", started_at=NOW))
        repository.acquire_run_lease(
            "abandoned",
            acquired_at=NOW,
            expires_at=datetime(2026, 8, 29, 11, 59, tzinfo=UTC),
        )
        assert runner.run("profile-a", request_id="recovered").status == "completed"
        assert repository.get_run("abandoned").status == "failed"
    finally:
        database.dispose()


def test_source_exception_releases_lease_for_following_run(tmp_path: Path) -> None:
    state = SourceState(complete_result(lot()), failure=RuntimeError("boom"))
    database, runner = engine(tmp_path, state, profile())
    try:
        assert runner.run("profile-a", request_id="failed-source").status == "failed"
        state.failure = None
        assert runner.run("profile-a", request_id="after-failure").status == "completed"
    finally:
        database.dispose()
