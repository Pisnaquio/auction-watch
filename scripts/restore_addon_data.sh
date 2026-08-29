#!/usr/bin/env bash
set -euo pipefail

data_dir="${AW_DATA_DIR:-/data}/auction-watch"
archive="${1:?usage: restore_addon_data.sh BACKUP.tar.gz}"
mkdir -p "$data_dir"

if tar -tzf "$archive" | awk 'BEGIN { bad=0 } { if ($0 ~ /^\// || $0 ~ /(^|\/)\.\.($|\/)/) bad=1 } END { exit bad }'; then
  tar -xzf "$archive" -C "$data_dir"
else
  printf 'backup contains an unsafe path\n' >&2
  exit 1
fi
