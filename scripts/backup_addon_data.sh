#!/usr/bin/env bash
set -euo pipefail

data_dir="${AW_DATA_DIR:-/data}/auction-watch"
output="${1:?usage: backup_addon_data.sh OUTPUT.tar.gz}"
if [[ ! -d "$data_dir" ]]; then
  printf 'add-on data directory does not exist\n' >&2
  exit 1
fi
tar -czf "$output" -C "$data_dir" .
