# Architecture

Auction Watch is a standalone, generic application with independent search
profiles and source adapters.

## Boundaries

- `core` owns immutable domain contracts, identity, normalization, and matching.
- `sources` owns transport, discovery, parsing, normalization, source metadata,
  and coverage reporting for one source at a time.
- `profiles` owns profile creation, cloning, import/export, and the locked
  system seed.
- `persistence` owns SQLite, Alembic, SQLAlchemy rows, lifecycle, matches,
  user state, and notification outbox.
- `server` owns HTTP API and readiness integration.
- `notifications` owns delivery after an outbox record is durably created.
- `web` is a client of the versioned API.
- `runner` is the only layer that coordinates profiles, sources, persistence,
  receipts, reconciliation, matching, lifecycle and snapshots.

The engine does not know whether a profile describes consoles, books, tools,
records, or another category. Sources do not know profiles, matching, email,
SQLite, or runtime files.

## Data flow

The source registry constructs each selected adapter once per run using an
injected transport. The adapter discovers groups, normalizes lots, and emits
coverage receipts. Orchestration persists the source result and reconciles
group lifecycle only when authority is proven. The matcher then evaluates each
normalized lot against every interested enabled profile and stores independent
profile matches. Publication and notification consume durable snapshots and
outbox records afterward.

The public adapters currently include five protocol-specific integrations:

- Bavastro uses the `published_auctions` JSON listing/detail API and paginated
  `/lots/published/` responses;
- Castells reads the `GXState` marker from its HTML home page, then calls the
  public `rest/API/Remate/lotes` JSON endpoint;
- Remotes parses the public RSS feed and its stable `lote` query identifiers;
- TodoRemates reads the WordPress `remate` taxonomy and WooCommerce Store API,
  following WordPress pagination headers;
- Prado reads the WooCommerce Store API and its auction price/status markup.

Each adapter owns its parsing and completeness proof; a protocol marker,
response shape, or pagination failure becomes partial/failed rather than an
authoritative empty inventory. To add a sixth source, implement
`BaseAuctionSource`, add its public `SourceSpec` to a registry, and provide
sanitized transport fixtures; the run, persistence, matcher, and notification
layers remain unchanged.

SQLite lives at `${AW_DATA_DIR}/auction-watch.sqlite3`. Alembic is the only
schema authority, and packaged migrations are resolved independently of the
checkout or current working directory. Docker and the future Home Assistant
add-on use the same application core; the run engine itself does not contact
Home Assistant, send email, start a daemon, or execute real scans in tests.

## Run engine boundary

`AuctionRunEngine` acquires a durable SQLite lease, records selected profile
revisions and the union of required sources, constructs each adapter once,
persists source results and receipts, reconciles groups only from persisted
receipts, matches the reconciled active inventory, and publishes a
hash-addressed snapshot. Complete authoritative discovery may close omitted
groups; partial, failed, timed-out, or structurally uncertain sources preserve
their previous inventory. Failed runs do not replace the last snapshot.

The scheduler helper only determines due profiles. It does not start a daemon,
perform network work, or send notifications.

Manual and scheduled requests enter `run_queue`; a single-process worker claims
them transactionally, resumes abandoned jobs after restart, and invokes the
same durable run engine. The notification planner compares persisted snapshots
and writes one logical SMTP outbox item per run/profile/channel when policy
requires it. Delivery is retried with bounded backoff through a sender
contract; the SMTP implementation is runtime-configured and the fake sender is
used by tests. The worker is opt-in through `AW_WORKER_ENABLED` so validation
and local startup cannot unexpectedly perform scans or send mail.

The versioned profile API and React web client expose this durable boundary:
profile CRUD uses optimistic revisions, manual runs require an idempotency key,
snapshots are read from persisted reconciled state, and opportunity decisions
are written through the operational repository. A timeout or partial run is
shown as degraded coverage by the client, never as an authoritative empty
result. Daemon scheduling, email, and Home Assistant deployment remain outside
this task.
