"""Durable SQLite persistence for Auction Watch."""

from auction_watch.persistence.contracts import (
    CoverageReceipt,
    GroupRecord,
    LotRecord,
    NotificationOutboxRecord,
    OpportunityLifecycle,
    ProfileMatchRecord,
    RunProfileRecord,
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
    RunLeaseBusyError,
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
    "RunLeaseBusyError",
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
    "RunProfileRecord",
    "SourceRecord",
    "SourceRunRecord",
    "UserOpportunityState",
    "UserStateRevisionConflict",
    "alembic_head",
    "create_sqlite_engine",
    "sqlite_path",
    "upgrade_head",
]
