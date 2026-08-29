# Auction Watch

Auction Watch is a standalone, profile-driven monitor for public auction
listings. The engine is intentionally generic: source adapters provide
normalized auction data and user profiles provide the search behavior.

The current foundation includes strict domain contracts and matching,
durable SQLite persistence, a locked public `consolas` system profile, generic
adapters for Bavastro, Castells, Remotes, TodoRemates, and Prado, and a
transactional run engine with durable leases and canonical snapshots.
HTTP profile endpoints, a daemon scheduler, notification delivery, a complete
UI, and Home Assistant packaging remain separate future tasks.

## Local development

Requirements: Python 3.12 and Node.js 22 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cd web && npm install && npm run build
cd ..
uvicorn auction_watch.main:app --reload --port 8789
```

The service exposes `/api/v1/health` and `/api/v1/readiness`. The readiness
endpoint requires a usable, fully migrated SQLite database at
`${AW_DATA_DIR}/auction-watch.sqlite3` (default `${AW_DATA_DIR}=/data`).

## Docker

```bash
docker compose up --build
```

The application is available at <http://localhost:8789> and persists data in
the named `auction-watch-data` volume mounted at `/data`.

## Project boundaries

The project is designed to run as one installable application with multiple
independent profiles. It has no dependency on a collection application,
desktop automation, or personal data.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/CONTRACTS.md](docs/CONTRACTS.md), [docs/PERSISTENCE.md](docs/PERSISTENCE.md),
and [SECURITY.md](SECURITY.md).
