"""SQLite engine lifecycle and connection configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

DATABASE_FILENAME = "auction-watch.sqlite3"
ALEMBIC_HEAD = "0001_profiles"


def sqlite_path(data_dir: Path) -> Path:
    return data_dir / DATABASE_FILENAME


def create_sqlite_engine(data_dir: Path) -> Engine:
    """Create a configured engine without opening a connection."""

    path = sqlite_path(data_dir)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


@dataclass
class Database:
    """Owned engine/session lifecycle for one data directory."""

    data_dir: Path
    engine: Engine
    sessions: sessionmaker[Session]

    @classmethod
    def open(cls, data_dir: Path) -> Database:
        data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(data_dir)
        return cls(data_dir, engine, sessionmaker(engine, expire_on_commit=False))

    def check_ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text("SELECT 1")).scalar_one()
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            return result == 1 and revision == ALEMBIC_HEAD
        except Exception:
            return False

    def dispose(self) -> None:
        self.engine.dispose()
