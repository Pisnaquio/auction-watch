"""SQLite engine lifecycle and connection configuration."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

DATABASE_FILENAME = "auction-watch.sqlite3"


def sqlite_path(data_dir: Path) -> Path:
    return data_dir / DATABASE_FILENAME


def create_sqlite_engine(data_dir: Path) -> Engine:
    """Create a configured engine without opening a connection."""

    path = sqlite_path(data_dir)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "isolation_level": None},
        future=True,
        json_serializer=lambda value: json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # A scan writes thousands of lots and can hold the single SQLite
            # writer for longer than a few seconds. Five was short enough that
            # dismissing a lot mid-run failed outright instead of waiting.
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def begin_explicit_transaction(connection: Connection) -> None:
        statement = "BEGIN IMMEDIATE" if connection.info.pop("begin_immediate", False) else "BEGIN"
        connection.exec_driver_sql(statement)

    return engine


RECLAIM_THRESHOLD_BYTES = 64 * 1024 * 1024


def reclaim_free_space(engine: Engine, *, threshold: int = RECLAIM_THRESHOLD_BYTES) -> int:
    """VACUUM when the file holds a meaningful amount of unused pages.

    SQLite never returns freed pages to the filesystem on its own, so dropping
    the duplicated snapshot payloads left hundreds of megabytes of holes in a
    file that still occupied the disk. Only worth the rewrite when there is real
    space to recover, hence the threshold.
    """

    # VACUUM cannot run inside a transaction, and this engine opens one
    # explicitly on begin, so talk to the driver directly: connect_args already
    # put the sqlite3 connection in autocommit.
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            try:
                # Freed pages sit in the WAL until it is folded back, and the
                # freelist reads empty until then.
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            free_pages = cursor.execute("PRAGMA freelist_count").fetchone()[0]
            page_size = cursor.execute("PRAGMA page_size").fetchone()[0]
            reclaimable = int(free_pages or 0) * int(page_size or 0)
            if reclaimable < threshold:
                return 0
            cursor.execute("VACUUM")
            # The rewrite lands in the WAL, so the file only gives the space
            # back to the filesystem once that is folded in.
            try:
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            return reclaimable
        finally:
            cursor.close()
    finally:
        raw.close()


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
            from auction_watch.persistence.migrations import alembic_head

            with self.engine.connect() as connection:
                result = connection.execute(text("SELECT 1")).scalar_one()
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            return result == 1 and revision == alembic_head()
        except Exception:
            return False

    def dispose(self) -> None:
        self.engine.dispose()
