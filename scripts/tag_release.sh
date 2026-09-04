#!/usr/bin/env bash
# Cut a release tag from main. This is what triggers .github/workflows/release.yml.
#
#   ./scripts/tag_release.sh            # tags v<version from pyproject.toml>
#   ./scripts/tag_release.sh --dry-run  # only run the checks
#
# Refuses unless: on main, clean tree, in sync with origin/main, version
# consistent across files, CHANGELOG has the section, tag does not exist yet.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dry_run=false
[[ "${1:-}" == "--dry-run" ]] && dry_run=true

cd "$root_dir"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "must be on main (currently on $branch)" >&2
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "working tree has uncommitted changes" >&2
  exit 1
fi

git fetch --quiet origin main --tags
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "local main is not in sync with origin/main (pull or push first)" >&2
  exit 1
fi

python3 scripts/check_release_version.py --changelog
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)"
tag="v$version"

if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "tag $tag already exists" >&2
  exit 1
fi

if [[ "$dry_run" == true ]]; then
  echo "dry run: would tag $(git rev-parse --short HEAD) as $tag"
  exit 0
fi

git tag -a "$tag" -m "Auction Watch $version"
git push origin "$tag"
echo "pushed $tag — release workflow: https://github.com/Pisnaquio/auction-watch/actions"
