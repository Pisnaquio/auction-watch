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
add-on use the same application core; this repository makes no operational
deployment or Home Assistant compatibility claim.
