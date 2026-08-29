#!/usr/bin/env python3
"""Validate Supervisor options without printing their values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from auction_watch.addon_config import AddonOptions


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("/data/options.json")
    try:
        options = json.loads(path.read_text(encoding="utf-8"))
        AddonOptions.model_validate(options)
    except (OSError, ValueError, TypeError):
        print("add-on configuration is invalid", file=sys.stderr)
        return 1
    print("add-on configuration is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
