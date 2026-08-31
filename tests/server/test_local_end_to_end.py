"""A local, deterministic run through the public API and actual worker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from auction_watch.config import Settings
from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.main import create_app
from auction_watch.runner import AuctionRunEngine
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.registry import SourceRegistry, SourceSpec

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class LocalFixtureSource(BaseAuctionSource):
    source_id = "bavastro"
    label = "Bavastro fixture"
    discovery_url = "https://fixture.invalid/"

    def scan(self) -> SourceScanResult:
        group = AuctionGroup(
            source_id=self.source_id,
            auction_id="fixture-auction",
            title="Fixture auction",
            url="https://fixture.invalid/auction",
            observed_at=NOW,
            active=True,
        )
        lot = AuctionLot(
            source_id=self.source_id,
            auction_id=group.auction_id,
            lot_id="fixture-lot",
            title="Console fixture",
            description="A deterministic local match",
            lot_url="https://fixture.invalid/lot",
            auction_url=group.url,
            observed_at=NOW,
            active=True,
        )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=(group,),
            lots=(lot,),
            discovery_status="complete",
            inventory_authoritative=True,
            receipts=(
                GroupReceipt(
                    group_id=group.auction_id,
                    status="complete",
                    inventory_authoritative=True,
                    lot_count=1,
                    error_count=0,
                    started_at=NOW,
                    finished_at=NOW,
                ),
            ),
        )


def fixture_engine(database, **kwargs):
    registry = SourceRegistry(
        (SourceSpec("bavastro", "Bavastro", lambda transport: LocalFixtureSource(transport)),)
    )
    return AuctionRunEngine(
        database,
        source_registry=registry,
        transport_factory=object,
        now=lambda: NOW,
        **kwargs,
    )


def profile_payload() -> dict[str, object]:
    return {
        "id": "local-smoke",
        "name": "Local smoke",
        "source_ids": ["bavastro"],
        "keywords_any": ["console"],
        "notification_mode": "disabled",
        "schedule": {"enabled": False, "times": [], "timezone": "UTC"},
    }


def test_local_api_queue_worker_snapshot_flow(tmp_path: Path) -> None:
    application = create_app(
        Settings(data_dir=tmp_path, worker_enabled=False), run_engine_factory=fixture_engine
    )
    with TestClient(application) as client:
        created = client.post("/api/v1/profiles", json={"profile": profile_payload()})
        assert created.status_code == 201
        queued = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "local-smoke-run"},
            json={"profile_id": "local-smoke"},
        )
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        result = application.state.worker.run_once()
        assert result is not None and result.status == "completed"
        completed = client.get(f"/api/v1/runs/{run_id}")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        snapshot = client.get("/api/v1/profiles/local-smoke/snapshot")
        assert snapshot.status_code == 200
        matches = snapshot.json()["payload"]["profiles"][0]["matches"]
        assert len(matches) == 1
