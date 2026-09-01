#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-$root_dir/dist/auction-watch-addon.tar.gz}"
mkdir -p "$(dirname "$output")"

if ! git -C "$root_dir" diff --quiet || ! git -C "$root_dir" diff --cached --quiet; then
  printf '%s\n' 'refusing to package uncommitted files' >&2
  exit 1
fi

files=(
  .dockerignore
  CHANGELOG.md
  Dockerfile
  README.md
  SECURITY.md
  build.yaml
  config.yaml
  docs/SEARCH_GUIDE.md
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
git -C "$root_dir" archive --format=tar HEAD -- "${files[@]}" | gzip -n > "$output"
python3 "$root_dir/scripts/audit_addon_artifact.py" "$output"
printf 'add-on package created: %s\n' "$output"
