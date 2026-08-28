"""Generic auction source adapters and their transport-independent contracts."""

from auction_watch.sources.adapters import (
    BavastroSource,
    CastellsSource,
    PradoSource,
    RemotesSource,
    TodoRematesSource,
)
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.registry import DEFAULT_SOURCE_REGISTRY, SourceRegistry, SourceSpec

__all__ = [
    "BavastroSource",
    "CastellsSource",
    "DEFAULT_SOURCE_REGISTRY",
    "GroupReceipt",
    "PradoSource",
    "RemotesSource",
    "SourceRegistry",
    "SourceScanResult",
    "SourceSpec",
    "TodoRematesSource",
]
