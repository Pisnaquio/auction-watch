from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from auction_watch.config import Settings
from auction_watch.main import create_app
from auction_watch.persistence.database import Database, sqlite_path
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.models import ProfileSourceRow
from auction_watch.persistence.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileRevisionConflictError,
)
from auction_watch.profiles.models import PriceFilter, SearchProfile


def make_profile(**overrides: object) -> SearchProfile:
    values: dict[str, object] = {
        "id": "vinilos-rock",
        "name": "Vinilos de rock argentino",
        "enabled": True,
        "keywords_any": ["vinilo", "LP"],
        "keywords_all": ["rock"],
        "exact_phrases": ["rock argentino"],
        "exclude_keywords": ["réplica"],
        "boost_keywords": {"Spinetta": 4, "Pescado Rabioso": 8},
        "source_ids": ["remates", "bavastro", "castells"],
        "minimum_score": 5,
        "price_filter": PriceFilter(
            maximum=Decimal("123.45"), currency="UYU", on_unknown="exclude"
        ),
        "notification_mode": "matches_or_failure",
        "schedule": {
            "enabled": True,
            "times": ["09:15", "17:10"],
            "timezone": "America/Montevideo",
        },
    }
    values.update(overrides)
    return SearchProfile(**values)


@pytest.fixture
def database(tmp_path: Path):
    db = Database.open(tmp_path)
    upgrade_head(tmp_path, db.engine)
    yield db
    db.dispose()


def test_empty_database_migrates_and_upgrade_is_idempotent(tmp_path: Path) -> None:
    path = sqlite_path(tmp_path)
    assert not path.exists()

    upgrade_head(tmp_path)
    upgrade_head(tmp_path)

    assert path.exists()
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert {"profiles", "profile_sources", "alembic_version"} <= tables
    assert revision == "0001_profiles"


def test_sqlite_pragmas_are_configured(database: Database) -> None:
    with database.engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()

    assert foreign_keys == 1
    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000
    assert synchronous == 1


def test_profile_repository_round_trip_and_order(database: Database) -> None:
    repository = ProfileRepository(database)
    created = repository.create(make_profile())
    loaded = repository.get(created.profile.id)

    assert loaded is not None
    assert loaded.profile == created.profile
    assert loaded.revision == 1
    assert loaded.profile.source_ids == ("remates", "bavastro", "castells")
    assert loaded.profile.price_filter is not None
    assert loaded.profile.price_filter.maximum == Decimal("123.45")
    assert loaded.profile.schedule.timezone == "America/Montevideo"
    assert loaded.created_at.tzinfo == UTC
    assert [item.profile.id for item in repository.list()] == ["vinilos-rock"]


def test_replace_increments_revision_and_delete_requires_revision(database: Database) -> None:
    repository = ProfileRepository(database)
    repository.create(make_profile())
    replacement = make_profile(name="Perfil actualizado", source_ids=["castells", "remates"])

    replaced = repository.replace(replacement, expected_revision=1)
    assert replaced.revision == 2
    assert replaced.profile.name == "Perfil actualizado"
    assert replaced.profile.source_ids == ("castells", "remates")

    with pytest.raises(ProfileRevisionConflictError):
        repository.delete(replacement.id, expected_revision=1)
    assert repository.get(replacement.id) is not None

    repository.delete(replacement.id, expected_revision=2)
    assert repository.get(replacement.id) is None
    with pytest.raises(ProfileNotFoundError):
        repository.delete(replacement.id, expected_revision=1)


def test_create_rejects_existing_id_and_replace_missing_id(database: Database) -> None:
    repository = ProfileRepository(database)
    profile = make_profile()
    repository.create(profile)
    with pytest.raises(ProfileAlreadyExistsError):
        repository.create(profile)
    with pytest.raises(ProfileNotFoundError):
        repository.replace(make_profile(id="otro-perfil"), expected_revision=1)


def test_revision_conflict_does_not_modify_any_row(database: Database) -> None:
    repository = ProfileRepository(database)
    created = repository.create(make_profile())

    with pytest.raises(ProfileRevisionConflictError):
        repository.replace(make_profile(name="No debe persistir"), expected_revision=99)

    current = repository.get(created.profile.id)
    assert current is not None
    assert current.revision == 1
    assert current.profile.name == created.profile.name


def test_source_insert_failure_rolls_back_profile(
    monkeypatch: pytest.MonkeyPatch, database: Database
) -> None:
    repository = ProfileRepository(database)

    def fail_insert(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated source failure")

    monkeypatch.setattr(repository, "_insert_sources", fail_insert)
    with pytest.raises(RuntimeError):
        repository.create(make_profile())
    assert repository.get("vinilos-rock") is None


def test_profile_sources_cascade_on_delete(database: Database) -> None:
    repository = ProfileRepository(database)
    created = repository.create(make_profile())
    with database.engine.connect() as connection:
        before = connection.execute(
            select(func.count()).select_from(ProfileSourceRow.__table__)
        ).scalar_one()
    repository.delete(created.profile.id, expected_revision=1)
    with database.engine.connect() as connection:
        after = connection.execute(
            select(func.count()).select_from(ProfileSourceRow.__table__)
        ).scalar_one()
    assert before == 3
    assert after == 0


def test_restart_engine_preserves_profiles(tmp_path: Path) -> None:
    first = Database.open(tmp_path)
    upgrade_head(tmp_path, first.engine)
    ProfileRepository(first).create(make_profile())
    first.dispose()

    second = Database.open(tmp_path)
    upgrade_head(tmp_path, second.engine)
    loaded = ProfileRepository(second).get("vinilos-rock")
    assert loaded is not None
    assert loaded.profile.name == "Vinilos de rock argentino"
    second.dispose()


def test_readiness_and_health_behavior(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    application = create_app(settings)
    with TestClient(application) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/readiness").status_code == 200

    invalid_data_dir = tmp_path / "not-a-directory"
    invalid_data_dir.write_text("occupied", encoding="utf-8")
    unavailable = create_app(Settings(data_dir=invalid_data_dir))
    with TestClient(unavailable) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/readiness").status_code == 503


def test_readiness_is_503_when_database_is_not_at_head(tmp_path: Path) -> None:
    path = sqlite_path(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('future_revision')")

    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        assert client.get("/api/v1/readiness").status_code == 503


def test_importing_persistence_does_not_create_sqlite(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["AW_DATA_DIR"] = str(tmp_path)
    subprocess.run(
        [sys.executable, "-c", "import auction_watch.persistence"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
    )
    assert not sqlite_path(tmp_path).exists()
