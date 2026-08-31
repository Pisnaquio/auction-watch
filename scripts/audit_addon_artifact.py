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
    ".storage",
}
FORBIDDEN_NAMES = {
    "options.json",
    "secrets.yaml",
    "credentials.json",
    "auth.json",
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
    re.compile(rb"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(rb"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE),
)
CONFIG_SECRET_CONTENT = re.compile(
    rb"(?:smtp_password|password|token|secret|api_key)\s*[:=]\s*"
    rb"[\"']?(?!(?:\*+|<redacted>|password\?)[\"'\s])[^\s#\"',}]{8,}",
    re.IGNORECASE,
)
CONFIG_SUFFIXES = {".env", ".json", ".toml", ".yaml", ".yml"}
MAX_MEMBER_BYTES = 2 * 1024 * 1024


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
            if pure.name.lower() in FORBIDDEN_NAMES or pure.name.lower().startswith(".env"):
                errors.append("secret-bearing configuration file in artifact")
            if name.endswith(FORBIDDEN_SUFFIXES):
                errors.append("runtime database or log in artifact")
            if member.issym() or member.islnk():
                errors.append("links are not allowed in artifact")
            if member.isfile() and member.size > MAX_MEMBER_BYTES:
                errors.append("unexpectedly large artifact member")
            if member.isfile() and name.endswith((".env", ".env.local")):
                errors.append("environment file in artifact")
            if member.isfile():
                payload = archive.extractfile(member)
                content = payload.read() if payload is not None else b""
                if any(pattern.search(content) for pattern in FORBIDDEN_CONTENT):
                    errors.append("secret value in artifact")
                if pure.suffix.lower() in CONFIG_SUFFIXES and CONFIG_SECRET_CONTENT.search(content):
                    errors.append("serialized secret value in artifact")
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
