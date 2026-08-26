"""Durable SQLite persistence for Auction Watch."""

from auction_watch.persistence.database import Database, create_sqlite_engine, sqlite_path
from auction_watch.persistence.migrations import alembic_head, upgrade_head
from auction_watch.persistence.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfilePersistenceError,
    ProfileRepository,
    ProfileRepositoryError,
    ProfileRevisionConflictError,
    StoredProfile,
)

__all__ = [
    "Database",
    "ProfileAlreadyExistsError",
    "ProfileNotFoundError",
    "ProfilePersistenceError",
    "ProfileRepository",
    "ProfileRepositoryError",
    "ProfileRevisionConflictError",
    "StoredProfile",
    "alembic_head",
    "create_sqlite_engine",
    "sqlite_path",
    "upgrade_head",
]
