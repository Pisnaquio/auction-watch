"""Shared validation for canonical and external identity components."""

from __future__ import annotations

import re

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
    if len(cleaned) > MAX_EXTERNAL_ID_LENGTH or any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{label} exceeds the supported identifier limit")
    return cleaned
