#!/usr/bin/env bash
# Run the API locally without using the Home Assistant add-on data directory.
#
#   ./scripts/dev_web_server.sh
#   AW_WEB_PORT=8790 ./scripts/dev_web_server.sh
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AW_DATA_DIR="${AW_DATA_DIR:-/tmp/auction-watch-dev-data}"
port="${AW_WEB_PORT:-8789}"

mkdir -p "$AW_DATA_DIR"
exec "$root_dir/.venv/bin/uvicorn" auction_watch.main:app --host 127.0.0.1 --port "$port"
