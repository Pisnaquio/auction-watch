"""Durable SQLite persistence for Auction Watch."""

from auction_watch.persistence.contracts import (
    CoverageReceipt,
    GroupRecord,
    LotRecord,
    NotificationOutboxRecord,
    OpportunityLifecycle,
    ProfileMatchRecord,
    RunRecord,
    SourceRecord,
    SourceRunRecord,
    UserOpportunityState,
)
from auction_watch.persistence.database import Database, create_sqlite_engine, sqlite_path
from auction_watch.persistence.migrations import alembic_head, upgrade_head
from auction_watch.persistence.operational_repository import (
    OperationalPersistenceError,
    OperationalRepository,
    ReconciliationReceiptError,
    UserStateRevisionConflict,
)
from auction_watch.persistence.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfilePersistenceError,
    ProfileRepository,
    ProfileRepositoryError,
    ProfileRevisionConflictError,
    StoredProfile,
    SystemProfileDeleteError,
    SystemProfileImmutableError,
)

__all__ = [
    "Database",
    "CoverageReceipt",
    "GroupRecord",
    "LotRecord",
    "NotificationOutboxRecord",
    "OperationalPersistenceError",
    "OperationalRepository",
    "ReconciliationReceiptError",
    "OpportunityLifecycle",
    "ProfileAlreadyExistsError",
    "ProfileNotFoundError",
    "ProfilePersistenceError",
    "ProfileRepository",
    "ProfileRepositoryError",
    "ProfileRevisionConflictError",
    "SystemProfileDeleteError",
    "SystemProfileImmutableError",
    "StoredProfile",
    "ProfileMatchRecord",
    "RunRecord",
    "SourceRecord",
    "SourceRunRecord",
    "UserOpportunityState",
    "UserStateRevisionConflict",
    "alembic_head",
    "create_sqlite_engine",
    "sqlite_path",
    "upgrade_head",
]
