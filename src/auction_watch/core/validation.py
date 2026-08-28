"""Shared validation for canonical and external identity components."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_EXTERNAL_ID_LENGTH = 256


def canonical_slug(value: object, label: str) -> str:
    """Validate an internal ASCII lowercase slug without linguistic coercion."""

    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise ValueError(f"{label} must be an ASCII lowercase slug")
    return value


def external_id(value: object, label: str) -> str:
    """Validate an opaque external identifier shared by models and identities."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > MAX_EXTERNAL_ID_LENGTH or any(
        unicodedata.category(char).startswith("C") for char in cleaned
    ):
        raise ValueError(f"{label} exceeds the supported identifier limit")
    return cleaned


def http_url(value: object, label: str, *, optional: bool = False) -> str | None:
    """Validate an absolute credential-free HTTP(S) URL."""

    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an absolute HTTP or HTTPS URL")
    cleaned = value.strip()
    if optional and not cleaned:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain credentials")
    return cleaned
