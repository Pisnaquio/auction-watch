"""Versioned, reversible identity keys for normalized opportunities."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

_KEY_PREFIX = "aw1:"
_ENCODED_COMPONENT = re.compile(r"(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+")


def _identity_component(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("identity components must be non-empty strings")
    return quote(value, safe="")


def encode_opportunity_key(source_id: str, auction_id: str, lot_id: str) -> str:
    """Encode the composite identity as a versioned, collision-free key."""

    components = (
        _identity_component(source_id),
        _identity_component(auction_id),
        _identity_component(lot_id),
    )
    return _KEY_PREFIX + ":".join(components)


def decode_opportunity_key(key: str) -> tuple[str, str, str]:
    """Decode and validate an ``aw1`` opportunity key."""

    if not isinstance(key, str) or not key.startswith(_KEY_PREFIX):
        raise ValueError("opportunity key must use the aw1 format")
    encoded_components = key[len(_KEY_PREFIX) :].split(":")
    if len(encoded_components) != 3 or any(
        _ENCODED_COMPONENT.fullmatch(component) is None for component in encoded_components
    ):
        raise ValueError("opportunity key has malformed encoded components")
    components = tuple(unquote(component) for component in encoded_components)
    if any(not component for component in components):
        raise ValueError("opportunity key components must not be empty")
    return components  # type: ignore[return-value]
