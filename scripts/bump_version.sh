#!/usr/bin/env bash
# Bump the version everywhere it lives, consistently.
#
#   ./scripts/bump_version.sh 0.1.17
#
# Touches pyproject.toml, config.yaml, src/auction_watch/__init__.py,
# web/package.json and web/package-lock.json. Does NOT touch CHANGELOG.md:
# write the "## X.Y.Z" entry yourself, the tag step refuses to run without it.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-}"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 MAJOR.MINOR.PATCH" >&2
  exit 2
fi

cd "$root_dir"

current="$(python3 scripts/check_release_version.py --quiet >/dev/null && sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)"
if [[ "$current" == "$version" ]]; then
  echo "already at $version" >&2
  exit 1
fi

sed -i '' "s/^version = \".*\"$/version = \"$version\"/" pyproject.toml
sed -i '' "s/^version: \".*\"$/version: \"$version\"/" config.yaml
sed -i '' "s/^__version__ = \".*\"$/__version__ = \"$version\"/" src/auction_watch/__init__.py
(cd web && npm version --no-git-tag-version --allow-same-version "$version" >/dev/null)

python3 scripts/check_release_version.py
printf '%s\n' "bumped $current -> $version" \
  "next: add a '## $version' section to CHANGELOG.md, open a PR, merge, then ./scripts/tag_release.sh"
