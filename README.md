# Auction Watch

Auction Watch is a standalone, profile-driven monitor for public auction
listings. The engine is intentionally generic: source adapters provide
normalized auction data and user profiles provide the search behavior.

The current foundation includes strict domain contracts and matching,
durable SQLite persistence, a locked public `consolas` system profile, generic
adapters for Bavastro, Castells, Remotes, TodoRemates, and Prado, and a
transactional run engine with durable leases and canonical snapshots.
HTTP profile endpoints, optional scheduler and notification delivery are
available. The Home Assistant add-on packaging is documented in
[docs/addon.md](docs/addon.md); it is an independent ingress application and
does not depend on Consolas.

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

To diagnose the public adapters without writing SQLite, matching, scheduler or
email delivery, run `python scripts/diagnose_sources.py`. Its output contains
only source status, counts, receipt state, and sanitized error types.

## Docker

```bash
docker compose up --build
```

The application is available at <http://localhost:8789> and persists data in
the named `auction-watch-data` volume mounted at `/data`.

For Home Assistant, use the add-on metadata in `config.yaml`. It stores data
under `/data/auction-watch`, runs migrations before startup, and keeps scans and
SMTP disabled by default. See [docs/addon.md](docs/addon.md) for installation,
backup, restoration, and troubleshooting.

## Releases

A release is a version bump merged to `main` followed by a `vX.Y.Z` tag; the
tag runs [release.yml](.github/workflows/release.yml), which verifies, packages
and publishes it. How the release then reaches Home Assistant is in
[docs/RELEASE.md](docs/RELEASE.md).

## Project boundaries

The project is designed to run as one installable application with multiple
independent profiles. It has no dependency on a collection application,
desktop automation, or personal data.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/CONTRACTS.md](docs/CONTRACTS.md), [docs/PERSISTENCE.md](docs/PERSISTENCE.md),
and [SECURITY.md](SECURITY.md).
