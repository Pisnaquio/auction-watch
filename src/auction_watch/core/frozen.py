"""Small dependency-free immutable mapping used by public domain models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn, TypeVar

Value = TypeVar("Value")


class FrozenDict[Value](dict[str, Value]):
    """A JSON-serializable dict that rejects all mutation methods."""

    def __init__(self, values: Mapping[str, Value] | None = None) -> None:
        super().__init__(sorted((values or {}).items()))

    def _immutable(self, *args: object, **kwargs: object) -> NoReturn:
        raise TypeError("mapping is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable

    def __ior__(self, other: object) -> FrozenDict[Value]:  # type: ignore[override,misc]
        self._immutable(other)
        return self
