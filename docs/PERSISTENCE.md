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
- `0005_run_engine`: run provenance, profile revisions, durable leases, and
  historical profile matches.
- `0006_profile_categories`: optional profile category filters.
- `0007_async_runs_notifications`: durable run queue and notification payloads.

Upgrades are safe to run repeatedly. Each engine enables foreign keys, WAL,
busy timeout, and explicit transaction boundaries. All timestamps are stored
as UTC-aware values at the contract boundary. Decimal amounts use a numeric
column with currency kept separately; URLs are validated before persistence.

## Operational schema

`auction_lots` has one row per canonical `(source_id, auction_id, lot_id)` and
is never copied per profile. `auction_groups` stores the source's stable group
metadata. `sources` stores public adapter metadata and enablement.

`runs` records the lifecycle of a manual, scheduled, or system run using
`queued`, `running`, `completed`, `partial`, or `failed`. `run_profiles` stores
the exact profile revision used, and `run_leases` provides durable expiry-based
cross-process exclusion. `run_sources`
records independent source status, discovered/processed/failed counts, and
whether that source result is authoritative. `coverage_receipts` records the
same decision at group granularity with `complete`, `partial`, or `failed`
coverage. `auction_snapshots` is an immutable, hash-addressed publication
payload associated with a run.

`run_queue` is the durable handoff from the versioned API or scheduler to the
single-process worker. Its unique idempotency key and transactionally claimed
status make retries safe; jobs left `running` by an interrupted process are
returned to `queued` on worker startup. The worker invokes the existing engine
with the persisted run ID, so engine idempotency prevents duplicate snapshots
or matches.

`opportunities` stores lifecycle separately from the normalized lot. It keeps
`first_seen_at`, `last_seen_at`, `seen_count`, active/removed state,
`removed_at`, and the last run that confirmed presence or authoritative
absence. `profile_matches` stores one match per profile and lot, including
score, matched terms/fields, active state, first/last match times, and runs
that confirmed presence or absence.

`user_opportunity_states` is keyed by profile plus lot, so following or
dismissing an opportunity in one profile never changes another profile. Its
positive revision is checked optimistically. User decisions are not deleted
when inventory disappears or reappears.

`notification_outbox` stores channel, profile, run/snapshot association,
deduplication key, delivery status (`pending`, `sending`, `sent`, `failed`, or
`uncertain`), notification type, sanitized payload, attempts, retry time, and
timestamps. A unique
deduplication key prevents duplicate logical deliveries. There are no
cascades from operational history, user state, or outbox rows that could erase
those records accidentally.

Notification planning compares the current persisted snapshot with the prior
one and creates an outbox item only for a new or materially changed match, or
for a configured failure. A partial/non-authoritative run never creates a
zero-result notification. Delivery is a separate bounded worker with
`pending`, `sending`, `sent`, and `failed` states and exponential backoff.
`AW_WORKER_ENABLED` is opt-in; SMTP settings are read only from the runtime
environment and are absent from the repository and logs.

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
authority only after the matching receipt is persisted. Complete authoritative
source discovery may close omitted groups. Partial or failed discovery cannot
close them. This keeps parsing, matching, and storage independent and makes
destructive transitions reviewable.

## Run engine transaction boundary

`AuctionRunEngine` is the sole coordinator. It scans each selected source once
per run, persists source and group receipts before reconciliation, evaluates
the active reconciled inventory against every selected profile, and creates a
snapshot only after that logical sequence succeeds. A run with no verifiable
source state is failed and leaves the previous snapshot untouched; a degraded
run publishes a partial snapshot with its coverage limitations. The API
enqueues work and exposes durable progress, while the optional worker and
notification sender consume those records. The Home Assistant add-on runs the
same migration chain under `/data/auction-watch`; installation itself does not
perform external scans or send notifications.

## Transaction and repository rules

The profile and operational repositories use explicit session transactions.
Foreign keys and database checks enforce status values, non-negative counts,
positive lifecycle revisions, and valid boolean/price states. Repository
contracts validate slugs, opaque external IDs, immutable identities, URLs,
currency, and UTC timestamps before rows are written. Optimistic conflicts are
reported instead of overwriting a concurrent profile or user decision.
