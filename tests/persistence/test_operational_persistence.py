from datetime import UTC, datetime
from pathlib import Path

import pytest

from auction_watch.persistence import (
    CoverageReceipt,
    Database,
    GroupRecord,
    LotRecord,
    NotificationOutboxRecord,
    OperationalRepository,
    RunRecord,
    SourceRecord,
    SourceRunRecord,
    UserOpportunityState,
    UserStateRevisionConflict,
    upgrade_head,
)
from auction_watch.persistence.repository import ProfileRepository
from auction_watch.profiles.models import SearchProfile

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def profile() -> SearchProfile:
    return SearchProfile(
        id="consolas", name="Consolas", source_ids=["remotes"], keywords_any=["console"]
    )


def lot(lot_id: str) -> LotRecord:
    return LotRecord(
        source_id="remotes",
        auction_id="auction:1",
        lot_id=lot_id,
        title=f"Console {lot_id}",
        lot_url=f"https://example.test/lots/{lot_id}",
        auction_url="https://example.test/auctions/1",
        observed_at=NOW,
    )


@pytest.fixture
def operational(tmp_path: Path) -> tuple[Database, OperationalRepository]:
    database = Database.open(tmp_path)
    upgrade_head(tmp_path, database.engine)
    ProfileRepository(database).create(profile())
    repository = OperationalRepository(database)
    repository.upsert_source(SourceRecord(source_id="remotes", label="Remotes"))
    repository.upsert_group(
        GroupRecord(
            source_id="remotes",
            group_id="auction:1",
            title="Auction",
            url="https://example.test/auctions/1",
            observed_at=NOW,
        )
    )
    yield database, repository
    database.dispose()


def test_group_reconciliation_is_authoritative_only_when_proven(operational) -> None:
    database, repository = operational
    repository.create_run(RunRecord(run_id="run-1", status="running", started_at=NOW))

    repository.reconcile_group(
        "run-1", "remotes", "auction:1", [lot("a"), lot("b")], authoritative=True
    )
    partial = repository.reconcile_group(
        "run-1", "remotes", "auction:1", [lot("a")], authoritative=False
    )
    assert {item.lot_id for item in partial if item.active} == {"a", "b"}
    complete = repository.reconcile_group(
        "run-1", "remotes", "auction:1", [lot("a")], authoritative=True
    )
    assert [(item.lot_id, item.active) for item in complete] == [("a", True), ("b", False)]
    database.dispose()


def test_receipts_user_state_and_outbox_are_durable_and_deduplicated(operational) -> None:
    database, repository = operational
    repository.create_run(RunRecord(run_id="run-1", status="running", started_at=NOW))
    repository.upsert_source_run(
        SourceRunRecord(run_id="run-1", source_id="remotes", status="succeeded", started_at=NOW)
    )
    repository.record_receipt(
        CoverageReceipt(
            run_id="run-1",
            source_id="remotes",
            group_id="auction:1",
            status="complete",
            inventory_authoritative=True,
            lot_count=1,
            error_count=0,
            started_at=NOW,
            finished_at=NOW,
        )
    )
    repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a")], authoritative=True)
    state = UserOpportunityState(
        profile_id="consolas",
        source_id="remotes",
        auction_id="auction:1",
        lot_id="a",
        created_at=NOW,
        updated_at=NOW,
    )
    assert repository.set_user_state(state).version == 1
    assert repository.set_user_state(state, expected_version=1).version == 2
    with pytest.raises(UserStateRevisionConflict):
        repository.set_user_state(state, expected_version=1)

    item = NotificationOutboxRecord(
        dedupe_key="run-1:consolas:email",
        channel="email",
        profile_id="consolas",
        run_id="run-1",
        created_at=NOW,
        updated_at=NOW,
    )
    first = repository.enqueue_notification(item)
    assert repository.enqueue_notification(item) == first
    database.dispose()
