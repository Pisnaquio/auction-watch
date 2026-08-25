"""Programmatic Alembic upgrade entry point."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from auction_watch.persistence.database import create_sqlite_engine, sqlite_path


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def upgrade_head(data_dir: Path, engine: Engine | None = None) -> None:
    """Idempotently upgrade the configured SQLite database to Alembic head."""

    data_dir.mkdir(parents=True, exist_ok=True)
    owned_engine = engine is None
    migration_engine = engine or create_sqlite_engine(data_dir)
    config = Config(str(_repository_root() / "alembic.ini"))
    config.set_main_option("script_location", str(_repository_root() / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{sqlite_path(data_dir)}")
    try:
        with migration_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        if owned_engine:
            migration_engine.dispose()
