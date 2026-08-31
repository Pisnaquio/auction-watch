from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from auction_watch.config import Settings
from auction_watch.core.identity import encode_opportunity_key
from auction_watch.main import create_app
from auction_watch.persistence.contracts import GroupRecord, LotRecord, RunRecord, SourceRecord
from auction_watch.persistence.database import Database
from auction_watch.persistence.operational_repository import OperationalRepository
from auction_watch.runner import RunOutcome


def profile_payload(profile_id: str = "libros") -> dict[str, object]:
    return {
        "id": profile_id,
        "name": "Libros usados",
        "source_ids": ["bavastro", "castells"],
        "keywords_any": ["libro", "novela"],
        "keywords_all": ["autor"],
        "exact_phrases": ["biblioteca de autor"],
        "exclude_keywords": ["réplica"],
        "categories": ["Literatura"],
        "boost_keywords": {"edición": 4},
        "risk_keywords": {"incompleto": 5},
        "context_rules": [],
        "minimum_score": 2,
        "price_filter": {"maximum": "1000", "currency": "UYU", "on_unknown": "include"},
        "notification_mode": "disabled",
        "schedule": {"enabled": True, "times": ["09:00"], "timezone": "UTC"},
    }


class FakeRunEngine:
    def __init__(self, database: Database, **_: object) -> None:
        self.operational = OperationalRepository(database)
        self.calls: list[tuple[str, str | None]] = []

    def run(self, profile_id: str, *, request_id: str | None, trigger: str) -> RunOutcome:
        self.calls.append((profile_id, request_id))
        now = datetime.now(UTC)
        existing = self.operational.get_run(request_id or "")
        if existing is None:
            self.operational.create_run(
                RunRecord(
                    run_id=request_id or "fake-run",
                    status="completed",
                    started_at=now,
                    finished_at=now,
                    trigger=trigger,
                    selected_sources=("bavastro",),
                )
            )
        else:
            self.operational.update_run(
                existing.model_copy(update={"status": "completed", "finished_at": now})
            )
        return RunOutcome(request_id or "fake-run", "completed", None, None)


def test_profile_api_protects_seed_and_supports_editable_crud(tmp_path: Path) -> None:
    application = create_app(
        Settings(data_dir=tmp_path, worker_enabled=False), run_engine_factory=FakeRunEngine
    )
    with TestClient(application) as client:
        listed = client.get("/api/v1/profiles")
        assert listed.status_code == 200
        seed = next(item for item in listed.json() if item["profile"]["id"] == "consolas")
        assert seed["protected"] is True
        paused = client.post("/api/v1/profiles/consolas/pause")
        assert paused.status_code == 200
        assert paused.json()["profile"]["enabled"] is False
        resumed = client.post("/api/v1/profiles/consolas/resume")
        assert resumed.status_code == 200
        assert resumed.json()["profile"]["enabled"] is True

        created = client.post("/api/v1/profiles", json={"profile": profile_payload()})
        assert created.status_code == 201
        assert created.json()["revision"] == 1
        assert created.json()["profile"]["categories"] == ["Literatura"]

        updated_payload = profile_payload()
        updated_payload["name"] = "Libros y novelas"
        updated = client.patch(
            "/api/v1/profiles/libros",
            json={"profile": updated_payload, "expected_revision": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        stale = client.patch(
            "/api/v1/profiles/libros",
            json={"profile": updated_payload, "expected_revision": 1},
        )
        assert stale.status_code == 409
        protected_update = client.patch(
            "/api/v1/profiles/consolas",
            json={
                "profile": {
                    **profile_payload("consolas"),
                    "kind": "system",
                    "locked": True,
                    "seed_key": "auction-watch-consolas",
                    "seed_version": 1,
                },
                "expected_revision": 1,
            },
        )
        assert protected_update.status_code == 403
        assert client.delete("/api/v1/profiles/consolas?expected_revision=1").status_code == 403

        clone = client.post(
            "/api/v1/profiles/consolas/clone", json={"new_id": "libros-clone"}
        )
        assert clone.status_code == 201
        assert clone.json()["protected"] is False


def test_run_idempotency_snapshot_and_opportunity_state_api(tmp_path: Path) -> None:
    application = create_app(
        Settings(data_dir=tmp_path, worker_enabled=False), run_engine_factory=FakeRunEngine
    )
    with TestClient(application) as client:
        assert (
            client.post("/api/v1/profiles", json={"profile": profile_payload()}).status_code
            == 201
        )
        first = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "request-1"},
            json={"profile_id": "libros"},
        )
        second = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "request-1"},
            json={"profile_id": "libros"},
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["run_id"] == second.json()["run_id"]
        run_id = first.json()["run_id"]
        assert first.json()["status"] == "queued"
        other_profile = profile_payload("discos")
        other_profile["name"] = "Discos"
        assert client.post("/api/v1/profiles", json={"profile": other_profile}).status_code == 201
        assert (
            client.post(
                "/api/v1/runs",
                headers={"Idempotency-Key": "request-1"},
                json={"profile_id": "discos"},
            ).status_code
            == 409
        )
        engine = application.state.run_engine
        assert engine.calls == []
        worker_result = application.state.worker.run_once()
        assert worker_result is not None
        run = client.get(f"/api/v1/runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "completed"
        assert client.get("/api/v1/profiles/libros/runs").json()[0]["run_id"] == run_id
        assert client.get("/api/v1/profiles/libros/notifications").status_code == 200

        database = application.state.database
        operational = OperationalRepository(database)
        key = encode_opportunity_key("bavastro", "auction:1", "lot/1")
        now = datetime.now(UTC)
        operational.upsert_source(SourceRecord(source_id="bavastro", label="Bavastro"))
        operational.upsert_group(
            GroupRecord(
                source_id="bavastro",
                group_id="auction:1",
                title="Subasta",
                url="https://example.test/auction/1",
                observed_at=now,
            )
        )
        operational.upsert_lot(
            LotRecord(
                source_id="bavastro",
                auction_id="auction:1",
                lot_id="lot/1",
                title="Libro",
                lot_url="https://example.test/lot/1",
                auction_url="https://example.test/auction/1",
                observed_at=now,
                active=True,
            )
        )
        operational.record_snapshot(
            f"{run_id}:snapshot",
            run_id,
            "hash",
            "completed",
            {
                "run": {"run_id": run_id, "status": "completed"},
                    "sources": [
                        {
                            "source_id": "bavastro",
                            "status": "complete",
                            "inventory_authoritative": True,
                            "errors": [],
                        }
                    ],
                "profiles": [{"profile_id": "libros", "matches": []}],
                "opportunities": [],
                "user_states": [],
            },
            published_at=datetime.now(UTC),
        )
        snapshot = client.get("/api/v1/profiles/libros/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["payload"]["run"]["run_id"] == run_id

        state = client.post(
            "/api/v1/profiles/libros/opportunities/state",
            json={"opportunity_key": key, "state": "follow"},
        )
        assert state.status_code == 200
        assert state.json()["state"] == "following"
        assert client.post(
            "/api/v1/profiles/libros/opportunities/state",
            json={"opportunity_key": key, "state": "discard", "expected_version": 1},
        ).json()["state"] == "dismissed"


def test_missing_idempotency_key_and_invalid_opportunity_key_are_rejected(tmp_path: Path) -> None:
    application = create_app(
        Settings(data_dir=tmp_path, worker_enabled=False), run_engine_factory=FakeRunEngine
    )
    with TestClient(application) as client:
        client.post("/api/v1/profiles", json={"profile": profile_payload()})
        assert client.post("/api/v1/runs", json={"profile_id": "libros"}).status_code == 400
        assert client.post(
            "/api/v1/profiles/libros/opportunities/state",
            json={"opportunity_key": "legacy-key", "state": "follow"},
        ).status_code == 422


def test_notification_api_redacts_delivery_payload_and_supports_protected_mode(
    tmp_path: Path,
) -> None:
    recipient = "recipient@example.test"
    password = "test-only-password"
    application = create_app(
        Settings(
            data_dir=tmp_path,
            worker_enabled=False,
            smtp_enabled=True,
            smtp_host="smtp.example.test",
            smtp_recipient=recipient,
            smtp_username="user",
            smtp_password=password,
        ),
        run_engine_factory=FakeRunEngine,
    )
    with TestClient(application) as client:
        mode = client.post(
            "/api/v1/profiles/consolas/notifications/mode",
            json={"mode": "matches_or_failure"},
        )
        assert mode.status_code == 200
        assert mode.json()["profile"]["notification_mode"] == "matches_or_failure"

        first = client.post("/api/v1/profiles/consolas/notifications/test")
        second = client.post("/api/v1/profiles/consolas/notifications/test")
        assert first.status_code == second.status_code == 202
        assert first.json()["dedupe_key"] == second.json()["dedupe_key"]
        listed = client.get("/api/v1/profiles/consolas/notifications")
        assert listed.status_code == 200
        exposed = first.text + second.text + listed.text
        assert recipient not in exposed
        assert password not in exposed
        assert "payload" not in listed.json()[0]
