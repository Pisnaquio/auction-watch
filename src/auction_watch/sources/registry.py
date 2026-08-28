"""Central typed source registry and deterministic adapter construction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from auction_watch.sources.adapters import (
    BavastroSource,
    CastellsSource,
    PradoSource,
    RemotesSource,
    TodoRematesSource,
)
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.transport import Transport


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    label: str
    factory: Callable[[Transport], BaseAuctionSource]


class SourceRegistry:
    def __init__(self, specs: tuple[SourceSpec, ...] = ()) -> None:
        self._specs: dict[str, SourceSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: SourceSpec) -> None:
        if spec.source_id in self._specs:
            raise ValueError(f"duplicate source_id: {spec.source_id}")
        self._specs[spec.source_id] = spec

    def specs(self) -> tuple[SourceSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def select(
        self, source_ids: tuple[str, ...] | list[str] | None = None
    ) -> tuple[SourceSpec, ...]:
        if source_ids is None:
            return self.specs()
        unknown = sorted(set(source_ids) - self._specs.keys())
        if unknown:
            raise ValueError(f"unknown source_id: {', '.join(unknown)}")
        return tuple(self._specs[source_id] for source_id in source_ids)

    def build(
        self, transport: Transport, source_ids: tuple[str, ...] | list[str] | None = None
    ) -> tuple[BaseAuctionSource, ...]:
        return tuple(spec.factory(transport) for spec in self.select(source_ids))


DEFAULT_SOURCE_REGISTRY = SourceRegistry(
    (
        SourceSpec("bavastro", "Bavastro", BavastroSource),
        SourceSpec("castells", "Castells", CastellsSource),
        SourceSpec("prado", "Prado Subastas", PradoSource),
        SourceSpec("remotes", "Remotes", RemotesSource),
        SourceSpec("todoremates", "TodoRemates", TodoRematesSource),
    )
)

__all__ = ["DEFAULT_SOURCE_REGISTRY", "SourceRegistry", "SourceSpec"]
