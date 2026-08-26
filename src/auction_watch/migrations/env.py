"""Alembic environment for the standalone SQLite schema."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection

from auction_watch.persistence.database import create_sqlite_engine, sqlite_path
from auction_watch.persistence.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _data_dir() -> Path:
    return Path(os.environ.get("AW_DATA_DIR", "/data"))


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)


def run_migrations_offline() -> None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{sqlite_path(data_dir)}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _configure(supplied_connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(data_dir)
    try:
        with engine.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
