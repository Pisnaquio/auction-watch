"""Fail when tracked source/config files contain forbidden local references."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKED_DIRS = ("src", "web/src", "tests", "packaging", "migrations")
CHECKED_FILES = ("Dockerfile", "docker-compose.yml", ".env.example", "pyproject.toml")
PATTERNS = (
    re.compile(r"console-collection", re.IGNORECASE),
    re.compile(r"/Users/"),
    re.compile(r"homeassistant\.local", re.IGNORECASE),
    re.compile(r"Mail\.app", re.IGNORECASE),
    re.compile(r"launchd", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def files_to_check() -> list[Path]:
    paths = [ROOT / name for name in CHECKED_FILES]
    for directory in CHECKED_DIRS:
        paths.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".pyi", ".ts", ".tsx", ".css", ".yml", ".yaml"}
        )
    return paths


def main() -> int:
    violations: list[str] = []
    for path in files_to_check():
        text = path.read_text(encoding="utf-8")
        for pattern in PATTERNS:
            if pattern.search(text):
                violations.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    if violations:
        print("Forbidden references found:")
        print("\n".join(violations))
        return 1
    print("Public safety audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
