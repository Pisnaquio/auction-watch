"""Pure text normalization helpers used by profiles and matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize case, accents, punctuation, and whitespace for matching."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized_chars = [
        char if char.isalnum() else " "
        for char in without_marks
    ]
    return _WHITESPACE.sub(" ", "".join(normalized_chars)).strip()


def normalize_term(value: str) -> str:
    """Normalize one user term while keeping its word sequence intact."""

    return normalize_text(value)


def normalize_phrase(value: str) -> str:
    """Normalize a phrase; phrase order is preserved by the normalized spaces."""

    return normalize_text(value)


def tokenize(value: str) -> tuple[str, ...]:
    """Return normalized word tokens with word-boundary semantics."""

    normalized = normalize_text(value)
    return tuple(normalized.split()) if normalized else ()


def contains_term(text: str, term: str) -> bool:
    """Return whether a normalized term occurs as complete word tokens."""

    haystack = tokenize(text)
    needle = tokenize(term)
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def dedupe_terms(values: Iterable[str]) -> list[str]:
    """Remove blank and normalized duplicate terms while preserving first spelling."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        readable = " ".join(value.strip().split())
        normalized = normalize_term(readable)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(readable)
    return result
