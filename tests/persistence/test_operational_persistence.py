from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import pytest
from sqlalchemy import event

from auction_watch.persistence import (
    CoverageReceipt,
    Database,
    GroupRecord,
    LotRecord,
    NotificationOutboxRecord,
    OperationalRepository,
    ReconciliationReceiptError,
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
    repository.upsert_source_run(
        SourceRunRecord(run_id="run-1", source_id="remotes", status="running", started_at=NOW)
    )

    def receipt(status: str, authoritative: bool, count: int) -> None:
        repository.record_receipt(
            CoverageReceipt(
                run_id="run-1",
                source_id="remotes",
                group_id="auction:1",
                status=status,
                inventory_authoritative=authoritative,
                lot_count=count,
                error_count=0,
                started_at=NOW,
                finished_at=NOW,
            )
        )

    receipt("complete", True, 2)
    repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a"), lot("b")])
    receipt("partial", False, 1)
    partial = repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a")])
    assert {item.lot_id for item in partial if item.active} == {"a", "b"}
    receipt("complete", True, 1)
    complete = repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a")])
    assert [(item.lot_id, item.active) for item in complete] == [("a", True), ("b", False)]
    database.dispose()


def test_receipts_user_state_and_outbox_are_durable_and_deduplicated(operational) -> None:
    database, repository = operational
    repository.create_run(RunRecord(run_id="run-1", status="running", started_at=NOW))
    repository.upsert_source_run(
        SourceRunRecord(run_id="run-1", source_id="remotes", status="running", started_at=NOW)
    )
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
    repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a")])
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


def test_reconciliation_requires_matching_receipt_and_is_idempotent(operational) -> None:
    database, repository = operational
    repository.create_run(RunRecord(run_id="run-1", status="running", started_at=NOW))
    repository.upsert_source_run(
        SourceRunRecord(run_id="run-1", source_id="remotes", status="running", started_at=NOW)
    )
    with pytest.raises(ReconciliationReceiptError):
        repository.reconcile_group("run-1", "remotes", "auction:1", [lot("a")])
    with pytest.raises(ValueError, match="belong"):
        repository.reconcile_group(
            "run-1", "remotes", "auction:1", [lot("a").model_copy(update={"auction_id": "other"})]
        )

    for count in (2, 1, 1):
        repository.record_receipt(
            CoverageReceipt(
                run_id="run-1",
                source_id="remotes",
                group_id="auction:1",
                status="complete",
                inventory_authoritative=True,
                lot_count=count,
                error_count=0,
                started_at=NOW,
                finished_at=NOW,
            )
        )
        lifecycle = repository.reconcile_group(
            "run-1",
            "remotes",
            "auction:1",
            [lot("a"), lot("b")] if count == 2 else [lot("a")],
            observed_at=NOW,
        )
        if count == 2:
            assert {item.lot_id: item.seen_count for item in lifecycle} == {"a": 1, "b": 1}
    assert {item.lot_id: item.seen_count for item in lifecycle} == {"a": 1, "b": 1}
    removed = next(item for item in lifecycle if item.lot_id == "b")
    assert removed.active is False
    assert removed.removed_at == NOW
    assert removed.last_absence_run_id == "run-1"
    database.dispose()


def test_reconciliation_has_no_caller_authority_parameter() -> None:
    assert "authoritative" not in signature(OperationalRepository.reconcile_group).parameters


def test_group_reconciliation_has_constant_select_count(operational) -> None:
    database, repository = operational
    repository.create_run(RunRecord(run_id="run-bulk", status="running", started_at=NOW))
    repository.upsert_source_run(
        SourceRunRecord(
            run_id="run-bulk", source_id="remotes", status="running", started_at=NOW
        )
    )
    lots = [lot(f"bulk-{index:03d}") for index in range(250)]
    repository.record_receipt(
        CoverageReceipt(
            run_id="run-bulk",
            source_id="remotes",
            group_id="auction:1",
            status="complete",
            inventory_authoritative=True,
            lot_count=len(lots),
            error_count=0,
            started_at=NOW,
            finished_at=NOW,
        )
    )
    selects = 0

    def count_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(database.engine, "before_cursor_execute", count_selects)
    try:
        lifecycle = repository.reconcile_group(
            "run-bulk", "remotes", "auction:1", lots, observed_at=NOW
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", count_selects)

    assert len(lifecycle) == 250
    assert selects <= 4


def test_outbox_deduplication_survives_concurrent_inserts(operational) -> None:
    from concurrent.futures import ThreadPoolExecutor

    database, repository = operational
    repository.create_run(RunRecord(run_id="run-1", status="running", started_at=NOW))
    repository.upsert_source_run(
        SourceRunRecord(run_id="run-1", source_id="remotes", status="running", started_at=NOW)
    )
    repository.record_receipt(
        CoverageReceipt(
            run_id="run-1",
            source_id="remotes",
            group_id="auction:1",
            status="complete",
            inventory_authoritative=True,
            lot_count=0,
            error_count=0,
            started_at=NOW,
            finished_at=NOW,
        )
    )
    item = NotificationOutboxRecord(
        dedupe_key="run-1:consolas:concurrent",
        channel="email",
        profile_id="consolas",
        run_id="run-1",
        created_at=NOW,
        updated_at=NOW,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _index: repository.enqueue_notification(item), range(2)))
    assert ids[0] == ids[1]
    database.dispose()
