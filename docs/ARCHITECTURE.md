# Architecture

Auction Watch is a standalone application with a generic engine and multiple
independent search profiles.

## Boundaries

- `core` owns normalized domain contracts and orchestration.
- `sources` owns discovery and parsing for one source at a time.
- `profiles` owns profile storage, import/export, and validation.
- `server` owns the HTTP API and SQLite integration.
- `notifications` owns delivery after a verified publication.
- `web` is a client of the versioned API.

The engine does not know whether a profile describes collectibles, books,
tools, records, or any other category.

## Data flow

Each source is queried once per run and produces normalized auction groups and
lots. The resulting inventory is then evaluated against every enabled profile
that selected that source. Matching is not implemented inside source adapters.

SQLite is the durable store under `/data`. It will contain source inventory,
profile matches, run state, snapshots, and the notification outbox as separate
concerns. A partial source failure cannot make valid data from another source
disappear.

The HTTP API is versioned under `/api/v1`. Docker and the future Home Assistant
add-on will run the same application core and image; packaging must not create
a functional fork.
