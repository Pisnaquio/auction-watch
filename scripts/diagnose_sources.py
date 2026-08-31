#!/usr/bin/env python3
"""Run read-only source probes without persistence, matching, or notifications.

The output intentionally contains counts and error classes only.  It never
prints listing content, URLs, response bodies, profiles, or configuration.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from auction_watch.sources import DEFAULT_SOURCE_REGISTRY
from auction_watch.sources.transport import HttpxTransport


def summary(source_id: str, result: Any) -> dict[str, object]:
    receipts = tuple(result.receipts)
    return {
        "source_id": source_id,
        "status": result.discovery_status,
        "groups": len(result.groups),
        "lots": len(result.lots),
        "receipts": {
            "complete": sum(item.status == "complete" for item in receipts),
            "partial": sum(item.status == "partial" for item in receipts),
            "failed": sum(item.status == "failed" for item in receipts),
        },
        "authoritative": result.inventory_authoritative,
        "error_types": sorted({str(error).split("(", 1)[0].strip() for error in result.errors}),
        "warning_types": sorted(
            {str(warning).split("(", 1)[0].strip() for warning in result.warnings}
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", help="source ids; defaults to all registered sources")
    args = parser.parse_args()
    selected = tuple(args.sources) or tuple(
        spec.source_id for spec in DEFAULT_SOURCE_REGISTRY.specs()
    )
    try:
        specs = DEFAULT_SOURCE_REGISTRY.select(selected)
    except ValueError as exc:
        parser.error(str(exc))

    transport = HttpxTransport()
    results: list[dict[str, object]] = []
    try:
        adapters = DEFAULT_SOURCE_REGISTRY.build(transport, selected)
        for spec, adapter in zip(specs, adapters, strict=True):
            try:
                results.append(summary(spec.source_id, adapter.scan()))
            except Exception as exc:  # The diagnostic must report one source and continue.
                results.append(
                    {
                        "source_id": spec.source_id,
                        "status": "failed",
                        "groups": 0,
                        "lots": 0,
                        "receipts": {"complete": 0, "partial": 0, "failed": 0},
                        "authoritative": False,
                        "error_types": [type(exc).__name__],
                        "warning_types": [],
                    }
                )
    finally:
        transport.close()
    print(json.dumps({"sources": results}, sort_keys=True))
    return 0 if all(item["status"] == "complete" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
