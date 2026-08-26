"""Programmatic Alembic upgrades backed by packaged resources."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from auction_watch.persistence.database import create_sqlite_engine, sqlite_path


@contextmanager
def _alembic_config(data_dir: Path) -> Iterator[Config]:
    resource = files("auction_watch.migrations")
    with as_file(resource) as migration_dir:
        config = Config()
        config.set_main_option("script_location", str(migration_dir))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{sqlite_path(data_dir)}")
        yield config


def alembic_head() -> str:
    """Return the single Alembic head derived from packaged revisions."""

    with _alembic_config(Path("/data")) as config:
        heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Auction Watch requires exactly one Alembic head")
    return heads[0]


def upgrade_head(data_dir: Path, engine: Engine | None = None) -> None:
    """Idempotently upgrade the configured SQLite database to Alembic head."""

    data_dir.mkdir(parents=True, exist_ok=True)
    owned_engine = engine is None
    migration_engine = engine or create_sqlite_engine(data_dir)
    try:
        with _alembic_config(data_dir) as config:
            with migration_engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
    finally:
        if owned_engine:
            migration_engine.dispose()
