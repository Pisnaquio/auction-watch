"""Home Assistant add-on bootstrap without changing project defaults."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

from auction_watch.addon_config import AddonOptions
from auction_watch.persistence.database import Database
from auction_watch.persistence.migrations import upgrade_head

OPTIONS_PATH = Path("/data/options.json")
ADDON_DATA_DIR = Path("/data/auction-watch")


def load_options(path: Path = OPTIONS_PATH) -> AddonOptions:
    """Load and validate Supervisor options without exposing their contents."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return AddonOptions.model_validate(payload)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("invalid add-on configuration") from exc


def apply_environment(
    options: AddonOptions, environment: MutableMapping[str, str] | None = None
) -> MutableMapping[str, str]:
    """Map validated options into the process environment for the app."""

    target = environment if environment is not None else os.environ
    values = {
        "AW_DATA_DIR": str(ADDON_DATA_DIR),
        "AW_HOST": "0.0.0.0",
        "AW_PORT": "8789",
        "AW_WORKER_ENABLED": "true",
        "AW_TIMEZONE": options.timezone,
        "AW_SCHEDULER_ENABLED": str(options.scheduler_enabled).lower(),
        "AW_WORKER_POLL_SECONDS": str(options.worker_poll_seconds),
        "AW_SMTP_ENABLED": str(options.smtp_enabled).lower(),
        "AW_SMTP_PORT": str(options.smtp_port),
        "AW_SMTP_SENDER": options.smtp_sender,
        "AW_SMTP_USE_TLS": str(options.smtp_use_tls).lower(),
    }
    optional = (
        {
            "AW_SMTP_HOST": options.smtp_host,
            "AW_SMTP_RECIPIENT": options.smtp_recipient,
            "AW_SMTP_USERNAME": options.smtp_username,
            "AW_SMTP_PASSWORD": options.smtp_password,
        }
        if options.smtp_enabled
        else {}
    )
    smtp_keys = (
        "AW_SMTP_HOST",
        "AW_SMTP_RECIPIENT",
        "AW_SMTP_USERNAME",
        "AW_SMTP_PASSWORD",
    )
    for key in smtp_keys:
        if key not in optional:
            target.pop(key, None)
    for key, value in optional.items():
        if value is not None:
            values[key] = value
        else:
            target.pop(key, None)
    target.update(values)
    return target


def migrate_data(data_dir: Path = ADDON_DATA_DIR) -> None:
    """Run the idempotent migration chain for the add-on data directory."""

    database = Database.open(data_dir)
    try:
        upgrade_head(data_dir, database.engine)
    finally:
        database.dispose()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"migrate", "serve"}:
        raise SystemExit("usage: python -m auction_watch.addon_runtime migrate|serve")
    options = load_options()
    apply_environment(options)
    if sys.argv[1] == "migrate":
        migrate_data()
        return
    os.execvp(
        "uvicorn",
        ["uvicorn", "auction_watch.main:app", "--host", "0.0.0.0", "--port", "8789"],
    )


if __name__ == "__main__":
    main()
