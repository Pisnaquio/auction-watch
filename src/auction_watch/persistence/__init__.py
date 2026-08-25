"""Durable SQLite persistence for Auction Watch."""

from auction_watch.persistence.database import Database, create_sqlite_engine, sqlite_path
from auction_watch.persistence.migrations import upgrade_head
from auction_watch.persistence.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileRepositoryError,
    ProfileRevisionConflictError,
    StoredProfile,
)

__all__ = [
    "Database",
    "ProfileAlreadyExistsError",
    "ProfileNotFoundError",
    "ProfileRepository",
    "ProfileRepositoryError",
    "ProfileRevisionConflictError",
    "StoredProfile",
    "create_sqlite_engine",
    "sqlite_path",
    "upgrade_head",
]
