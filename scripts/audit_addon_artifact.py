#!/usr/bin/env python3
"""Audit a packaged add-on without opening or executing its runtime."""

from __future__ import annotations

import re
import sys
import tarfile
from pathlib import PurePosixPath

REQUIRED = {
    "config.yaml",
    "Dockerfile",
    "rootfs/etc/cont-init.d/10-auction-watch",
    "rootfs/etc/services.d/auction-watch/run",
    "src/auction_watch/main.py",
    "web/package-lock.json",
}
FORBIDDEN_PARTS = {
    ".env",
    ".git",
    "__pycache__",
    "node_modules",
    "data",
    "logs",
    "snapshots",
    "results",
    "runtime-data",
}
FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db-wal",
    ".db-shm",
    ".log",
    ".pyc",
)
FORBIDDEN_CONTENT = (
    re.compile(rb"HOMEASSISTANT_TOKEN\s*[:=]\s*\S+"),
    re.compile(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(rb"AW_SMTP_PASSWORD\s*=\s*\S+"),
)


def audit(path: str) -> list[str]:
    errors: list[str] = []
    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        return [f"cannot read artifact: {type(exc).__name__}"]
    with archive:
        names = set()
        for member in archive.getmembers():
            name = member.name
            names.add(name)
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                errors.append("path traversal in artifact")
            if set(pure.parts) & FORBIDDEN_PARTS:
                errors.append("private runtime path in artifact")
            if name.endswith(FORBIDDEN_SUFFIXES):
                errors.append("runtime database or log in artifact")
            if member.issym() or member.islnk():
                errors.append("links are not allowed in artifact")
            if member.isfile() and member.size > 10 * 1024 * 1024:
                errors.append("unexpectedly large artifact member")
            if member.isfile() and name.endswith((".env", ".env.local")):
                errors.append("environment file in artifact")
            if member.isfile() and member.size <= 2 * 1024 * 1024:
                payload = archive.extractfile(member)
                content = payload.read() if payload is not None else b""
                if any(pattern.search(content) for pattern in FORBIDDEN_CONTENT):
                    errors.append("secret value in artifact")
        errors.extend(f"missing required member: {item}" for item in sorted(REQUIRED - names))
    return sorted(set(errors))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_addon_artifact.py ARCHIVE", file=sys.stderr)
        return 2
    errors = audit(sys.argv[1])
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("add-on artifact audit: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
