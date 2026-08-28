# Persistence

Auction Watch uses SQLite as a durable boundary. Pydantic records are the
public contracts; SQLAlchemy rows are internal and Alembic is the only schema
authority. The application never uses `metadata.create_all()`.

## Database and migrations

SQLite is stored at `${AW_DATA_DIR}/auction-watch.sqlite3` (`/data` by
default). The directory is created during startup, then packaged Alembic
revisions are applied with `upgrade_head()`. Imports do not open SQLite or
create files. Readiness requires a reachable database and the packaged Alembic
head.

The migration chain is:

- `0001_profiles`: profiles and ordered profile sources;
- `0002_operational_domain`: sources, groups, normalized lots, runs, source
  results, coverage receipts, snapshots, opportunities, profile matches, user
  states, and notification outbox;
- `0003_profile_kinds`: system/user profile metadata and versioned seed fields.
- `0004_contextual_profile_rules`: reusable risk weights and context gates.

Upgrades are safe to run repeatedly. Each engine enables foreign keys, WAL,
busy timeout, and explicit transaction boundaries. All timestamps are stored
as UTC-aware values at the contract boundary. Decimal amounts use a numeric
column with currency kept separately; URLs are validated before persistence.

## Operational schema

`auction_lots` has one row per canonical `(source_id, auction_id, lot_id)` and
is never copied per profile. `auction_groups` stores the source's stable group
metadata. `sources` stores public adapter metadata and enablement.

`runs` records the lifecycle of a manual or scheduled scan. `run_sources`
records independent source status, discovered/processed/failed counts, and
whether that source result is authoritative. `coverage_receipts` records the
same decision at group granularity with `complete`, `partial`, or `failed`
coverage. `auction_snapshots` is an immutable publication payload associated
with a run.

`opportunities` stores lifecycle separately from the normalized lot. It keeps
`first_seen_at`, `last_seen_at`, `seen_count`, active/removed state,
`removed_at`, and the last run that confirmed presence or authoritative
absence. `profile_matches` stores one match per profile and lot, including
score, matched terms/fields, and timestamps.

`user_opportunity_states` is keyed by profile plus lot, so following or
dismissing an opportunity in one profile never changes another profile. Its
positive revision is checked optimistically. User decisions are not deleted
when inventory disappears or reappears.

`notification_outbox` stores channel, profile, run/snapshot association,
deduplication key, delivery status (`pending`, `sending`, `sent`, `failed`, or
`uncertain`), attempts, sanitized error, retry time, and timestamps. A unique
deduplication key prevents duplicate logical deliveries. There are no
cascades from operational history, user state, or outbox rows that could erase
those records accidentally.

## Fail-closed inventory reconciliation

The repository reconciles one group in one transaction. It always upserts
valid lots and touches their lifecycle. It marks omitted active lots removed
only when the caller supplies a complete, authoritative receipt for that same
group. Partial, failed, omitted, or structurally suspicious discovery keeps
the previous inventory. An explicitly complete empty discovery may therefore
close a previously populated group, while an unexplained empty response may
not.

The source layer is responsible for proving this authority with a
`SourceScanResult` and per-group `GroupReceipt`; the persistence layer accepts
only the explicit boolean supplied by orchestration. This keeps parsing,
matching, and storage independent and makes the destructive transition
reviewable.

## Transaction and repository rules

The profile and operational repositories use explicit session transactions.
Foreign keys and database checks enforce status values, non-negative counts,
positive lifecycle revisions, and valid boolean/price states. Repository
contracts validate slugs, opaque external IDs, immutable identities, URLs,
currency, and UTC timestamps before rows are written. Optimistic conflicts are
reported instead of overwriting a concurrent profile or user decision.
