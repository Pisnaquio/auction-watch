# Persistence

The first durable layer persists search profiles only. It intentionally does
not create opportunity, match, run, snapshot, or outbox tables.

## Database location

SQLite is stored at:

```text
${AW_DATA_DIR}/auction-watch.sqlite3
```

`AW_DATA_DIR` defaults to `/data`. The application creates the directory during
startup before running migrations. Importing persistence modules does not open
SQLite or create a file.

## Initial schema

Alembic revision `0001_profiles` creates:

- `profiles`: validated profile fields, JSON arrays/objects, optional price
  values stored as text, schedule metadata, revision, and UTC timestamps;
- `profile_sources`: ordered source IDs with a composite primary key and a
  unique `(profile_id, position)` constraint;
- `alembic_version`: Alembic's schema revision marker.

SQLAlchemy models in `auction_watch.persistence.models` are separate from the
Pydantic domain models. `metadata.create_all()` is not used. Alembic is the
only schema authority, both through the normal command and the programmatic
`upgrade_head()` entry point. The Alembic environment, template, and revisions
are packaged under `auction_watch.migrations` and resolved with
`importlib.resources`; there is no dependency on the checkout or current
working directory. `alembic_head()` derives the readiness revision from those
packaged revisions instead of duplicating a revision constant.

## SQLite configuration

Every engine connection enables:

- `foreign_keys=ON`;
- `journal_mode=WAL`;
- `busy_timeout=5000`;
- `synchronous=NORMAL`;
- `check_same_thread=False`.

The repository uses explicit session transactions. A failed source-row insert
rolls back the profile row in the same transaction. Deleting a profile relies
on the foreign-key cascade for its ordered source rows.

SQLite runs with driver autocommit disabled at the SQLAlchemy boundary and an
explicit `BEGIN` event for every transaction. This gives each repository read
one snapshot across the profile and its ordered source rows. Replacements use
one conditional `UPDATE ... WHERE id AND revision`; only the transaction that
updates one row can replace its source rows and advance the revision. Deletes
use the same conditional pattern. A stale writer is reported as a revision
conflict, while an absent profile is reported as not found. Other integrity
failures remain persistence errors rather than being misclassified as duplicate
profiles.

## Revisions and repository API

`StoredProfile` is an immutable wrapper around a validated `SearchProfile` plus
revision and UTC timestamps. `create`, `replace`, and `delete` use optimistic
revision checks. `replace` increments the revision and replaces all source
rows atomically; stale revisions raise a specific conflict error instead of
overwriting data.

Reads reconstruct a fresh Pydantic profile, preserving Unicode, Decimal price
values, mapping immutability, ordered terms/sources, schedules, and notification
mode. Empty price filters (`{}` or `PriceFilter()` without a maximum or
currency) canonicalize to `None`, so `price_on_unknown` is never persisted by
itself. SQLAlchemy's JSON serializer uses UTF-8 characters directly, stable
object key ordering, and compact deterministic output while preserving arrays.
SQLAlchemy rows never leave the persistence layer.

## Lifecycle and readiness

`create_app()` opens no database during module import. Its FastAPI lifespan
resolves settings, opens the engine, runs `upgrade head`, initializes the
profile repository, and disposes the engine during shutdown. `/health` remains
process liveness only. `/readiness` performs a simple SQLite query and checks
that `alembic_version` is the expected head without exposing paths or SQL.

If the database cannot be opened or is not at head, readiness returns 503 while
the liveness endpoint remains available. Container health checks call readiness,
so a migration failure cannot be reported as a healthy application.

## Current limits

This stage has no profile HTTP endpoints, authentication, source adapters,
opportunity persistence, scheduler, notifications, or migration downgrade
automation. Future schema changes must arrive as new Alembic revisions.
