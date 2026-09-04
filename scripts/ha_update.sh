#!/usr/bin/env bash
# Update the live Home Assistant add-on to a released version, over SSH.
#
#   ./scripts/ha_update.sh 0.1.17                # reload store, update, wait until started
#   ./scripts/ha_update.sh 0.1.17 --rebuild      # same version already installed: rebuild from source
#   ./scripts/ha_update.sh --enable-auto-update  # one-time: let Supervisor apply future releases itself
#
# Environment:
#   AW_HA_SSH_HOST    SSH host alias (default: homeassistant)
#   AW_HA_ADDON_SLUG  installed add-on slug (default: 9b3464ac_auctionwatch)
#   AW_HA_TIMEOUT     seconds to wait for the update (default: 900)
#
# The add-on is a Supervisor *repository* add-on: Supervisor only sees a new
# version once config.yaml's `version:` changed on GitHub, so run this after
# the release workflow finished for that tag.
set -euo pipefail

host="${AW_HA_SSH_HOST:-homeassistant}"
slug="${AW_HA_ADDON_SLUG:-9b3464ac_auctionwatch}"
timeout="${AW_HA_TIMEOUT:-900}"
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes)

expected=""
rebuild=false
enable_auto=false
for arg in "$@"; do
  case "$arg" in
    --rebuild) rebuild=true ;;
    --enable-auto-update) enable_auto=true ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) expected="$arg" ;;
  esac
done

remote() { ssh "${ssh_opts[@]}" "$host" "$@"; }

info() {
  remote "ha apps info '$slug' --raw-json" | python3 -c '
import json, sys
data = json.load(sys.stdin)["data"]
print(data["version"], data["version_latest"], data["state"], data["auto_update"])
'
}

if [[ "$enable_auto" == true ]]; then
  remote "curl -sf -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H 'Content-Type: application/json' \
    http://supervisor/addons/$slug/options -d '{\"auto_update\": true}'" >/dev/null
  read -r _ _ _ auto <<<"$(info)"
  echo "auto_update: $auto"
  [[ -n "$expected" ]] || exit 0
fi

if [[ -z "$expected" ]]; then
  echo "usage: $0 MAJOR.MINOR.PATCH [--rebuild] | --enable-auto-update" >&2
  exit 2
fi

remote "ha store reload" >/dev/null
read -r installed latest state _ <<<"$(info)"
echo "installed: $installed  available: $latest  state: $state"

if [[ "$installed" == "$expected" && "$rebuild" != true ]]; then
  echo "already at $expected, nothing to do (use --rebuild to rebuild from source)"
  exit 0
fi

if [[ "$rebuild" == true ]]; then
  remote "ha apps rebuild '$slug'"
elif [[ "$latest" != "$expected" ]]; then
  cat >&2 <<EOF
Supervisor does not offer $expected (latest it sees: $latest).
Either the release workflow for v$expected has not finished, config.yaml's
version was not bumped, or Supervisor's store cache is stale — retry in a minute.
EOF
  exit 1
else
  remote "ha apps update '$slug' --backup"
fi

deadline=$(( $(date +%s) + timeout ))
while :; do
  read -r installed _ state _ <<<"$(info)"
  if [[ "$installed" == "$expected" && "$state" == "started" ]]; then
    echo "ok: $slug is $installed and started"
    exit 0
  fi
  if (( $(date +%s) >= deadline )); then
    echo "timed out waiting for $expected/started (now $installed/$state)" >&2
    exit 1
  fi
  sleep 10
done
