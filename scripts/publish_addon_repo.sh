#!/usr/bin/env bash
# Publish a released version to the Home Assistant add-on distribution repo.
#
#   ./scripts/publish_addon_repo.sh v0.1.17            # open a PR
#   ./scripts/publish_addon_repo.sh v0.1.17 --merge    # open it and merge it
#   ./scripts/publish_addon_repo.sh v0.1.17 --dry-run  # show what would change
#
# Supervisor does NOT read this application repo. It reads
# github.com/Pisnaquio/auction-watch-ha-addon, whose `auctionwatch/` directory
# mirrors the add-on file set. A version is only offered to Home Assistant once
# that mirror carries it, so this step is what makes a release installable.
#
# Environment:
#   AW_ADDON_REPO   distribution repo (default: Pisnaquio/auction-watch-ha-addon)
#   AW_ADDON_DIR    directory inside it (default: auctionwatch, the add-on slug)
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
addon_repo="${AW_ADDON_REPO:-Pisnaquio/auction-watch-ha-addon}"
addon_dir="${AW_ADDON_DIR:-auctionwatch}"

tag="${1:-}"
merge=false
dry_run=false
for arg in "${@:2}"; do
  case "$arg" in
    --merge) merge=true ;;
    --dry-run) dry_run=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 vMAJOR.MINOR.PATCH [--merge|--dry-run]" >&2
  exit 2
fi
version="${tag#v}"

cd "$root_dir"
git fetch --quiet origin --tags
if ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "tag $tag does not exist — run ./scripts/tag_release.sh first" >&2
  exit 1
fi

tagged_version="$(git show "$tag:config.yaml" | sed -n 's/^version: "\(.*\)"$/\1/p')"
if [[ "$tagged_version" != "$version" ]]; then
  echo "config.yaml at $tag says $tagged_version, expected $version" >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The exact file set package_addon.sh ships, taken from the tag.
files=(
  .dockerignore CHANGELOG.md Dockerfile README.md SECURITY.md build.yaml
  config.yaml docs/SEARCH_GUIDE.md docs/addon.md pyproject.toml repository.yaml
  rootfs scripts/audit_addon_artifact.py scripts/validate_addon_options.py src
  web/index.html web/package-lock.json web/package.json web/src
  web/tsconfig.json web/vite.config.ts
)

gh repo clone "$addon_repo" "$work/dist" -- --quiet
cd "$work/dist"

# Replace the mirror wholesale so removals upstream propagate.
rm -rf "${addon_dir:?}"
mkdir -p "$addon_dir"
git -C "$root_dir" archive --format=tar "$tag" -- "${files[@]}" | tar -x -C "$addon_dir"

# The distribution repo publishes its own repository.yaml at the root; the
# application repo's copy would point Supervisor back at the wrong URL.
rm -f "$addon_dir/repository.yaml"

if git diff --quiet && [[ -z "$(git status --porcelain)" ]]; then
  echo "$addon_repo already matches $tag — nothing to publish"
  exit 0
fi

echo "=== changes to publish ==="
git add -A
git status --short

if [[ "$dry_run" == true ]]; then
  echo "dry run: no branch, commit or PR created"
  exit 0
fi

branch="release/$version"
git checkout -q -b "$branch"
git -c "user.name=$(git -C "$root_dir" config user.name)" \
    -c "user.email=$(git -C "$root_dir" config user.email)" \
    commit -q -m "release: publish Auction Watch $version

Mirrors $tag from Pisnaquio/auction-watch. Supervisor reads this
repository, so this is what makes $version installable."
git push -q -u origin "$branch"

notes="$(git -C "$root_dir" show "$tag:CHANGELOG.md" | awk -v v="## $version" '
  $0 == v { grab = 1; next }
  grab && /^## / { exit }
  grab { print }
')"
url="$(gh pr create --repo "$addon_repo" --head "$branch" \
  --title "release: publish Auction Watch $version" \
  --body "Mirrors \`$tag\` from [Pisnaquio/auction-watch](https://github.com/Pisnaquio/auction-watch/releases/tag/$tag).

Home Assistant Supervisor reads this repository, not the application repo, so \`$version\` is not installable until this merges.

$notes")"
echo "$url"

if [[ "$merge" == true ]]; then
  gh pr merge "$url" --squash --delete-branch
  echo "published $version — run ./scripts/ha_update.sh $version to apply it"
fi
