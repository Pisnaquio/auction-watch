#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-$root_dir/dist/auction-watch-addon.tar.gz}"
mkdir -p "$(dirname "$output")"

files=(
  .dockerignore
  CHANGELOG.md
  Dockerfile
  README.md
  SECURITY.md
  config.yaml
  docs/addon.md
  pyproject.toml
  repository.yaml
  rootfs
  scripts/audit_addon_artifact.py
  scripts/validate_addon_options.py
  src
  web/index.html
  web/package-lock.json
  web/package.json
  web/src
  web/tsconfig.json
  web/vite.config.ts
)
tar \
  --exclude='*/__pycache__' \
  --exclude='*/__pycache__/*' \
  --exclude='*.pyc' \
  -czf "$output" -C "$root_dir" "${files[@]}"
python3 "$root_dir/scripts/audit_addon_artifact.py" "$output"
printf 'add-on package created: %s\n' "$output"
