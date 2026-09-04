#!/usr/bin/env python3
"""Verify the version string is consistent everywhere it lives.

Stdlib only so it can run before dependencies are installed. Sources:
pyproject.toml, config.yaml, web/package.json, web/package-lock.json and
src/auction_watch/__init__.py. Optionally compare against a git tag and require
a matching CHANGELOG section.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _match(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise SystemExit(f"{path}: version line not found")
    return found.group(1)


def read_versions() -> dict[str, str]:
    package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "web/package-lock.json").read_text(encoding="utf-8"))
    return {
        "pyproject.toml": _match("pyproject.toml", r'^version = "([^"]+)"$'),
        "config.yaml": _match("config.yaml", r'^version: "([^"]+)"$'),
        "src/auction_watch/__init__.py": _match(
            "src/auction_watch/__init__.py", r'^__version__ = "([^"]+)"$'
        ),
        "web/package.json": str(package["version"]),
        "web/package-lock.json": str(lock["version"]),
        "web/package-lock.json (packages)": str(lock["packages"][""]["version"]),
    }


def changelog_section(version: str) -> str | None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## {re.escape(version)}\s*$\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    found = pattern.search(text)
    if not found:
        return None
    body = found.group(1).strip()
    return body or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="git tag to compare against, e.g. v0.1.17")
    parser.add_argument(
        "--changelog", action="store_true", help="require a non-empty CHANGELOG section"
    )
    parser.add_argument(
        "--print-notes", action="store_true", help="print the CHANGELOG section body"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    versions = read_versions()
    distinct = sorted(set(versions.values()))
    if len(distinct) != 1:
        for path, value in versions.items():
            print(f"{path}: {value}", file=sys.stderr)
        print("version mismatch across files", file=sys.stderr)
        return 1
    version = distinct[0]
    if not SEMVER.match(version):
        print(f"version is not MAJOR.MINOR.PATCH: {version}", file=sys.stderr)
        return 1

    if args.tag is not None:
        expected = f"v{version}"
        if args.tag != expected:
            print(
                f"tag {args.tag} does not match version {version} (expected {expected})",
                file=sys.stderr,
            )
            return 1

    notes = changelog_section(version)
    if (args.changelog or args.print_notes) and notes is None:
        print(f"CHANGELOG.md has no non-empty '## {version}' section", file=sys.stderr)
        return 1

    if args.print_notes:
        print(notes)
    elif not args.quiet:
        print(f"version {version} is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
