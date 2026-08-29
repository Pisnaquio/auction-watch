"""Small protocol-neutral helpers for source-specific parsers."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

_TAGS = re.compile(r"<[^>]+>")


def clean_text(value: Any) -> str:
    return " ".join(_TAGS.sub(" ", html.unescape(str(value or ""))).split())


def absolute_url(base: str, value: Any) -> str:
    return urljoin(base, clean_text(value))


def decimal_value(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    raw = clean_text(value).replace("$", "").replace("UYU", "").strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif "." in raw and len(raw.rsplit(".", 1)[1]) == 3:
        raw = raw.replace(".", "")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def normalize_currency(value: Any) -> str | None:
    """Map observed source labels to the domain's three-letter currencies."""

    raw = clean_text(value).upper()
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(character)
    )
    compact = re.sub(r"[\s._-]+", "", folded)
    if compact in {
        "$",
        "$U",
        "UYU",
        "PESO",
        "PESOS",
        "PESOURUGUAYO",
        "PESOSURUGUAYOS",
    }:
        return "UYU"
    if compact in {
        "USD",
        "U$S",
        "US$",
        "DOLAR",
        "DOLARES",
        "DOLLAR",
        "DOLLARS",
    }:
        return "USD"
    return None


def utc_datetime(value: Any, *, zone: str = "America/Montevideo") -> datetime | None:
    raw = clean_text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, pattern)
                parsed = parsed.replace(tzinfo=ZoneInfo(zone))
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(zone))
    return parsed.astimezone(UTC)


def first_image(value: Any, *, base: str) -> str | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                candidate = item.get("src") or item.get("url") or item.get("image")
                if candidate:
                    return absolute_url(base, candidate)
    if value:
        return absolute_url(base, value)
    return None
