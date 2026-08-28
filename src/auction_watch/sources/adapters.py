"""The five public auction adapters built on the generic source contract."""

from __future__ import annotations

from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import SourceScanResult


class BavastroSource(BaseAuctionSource):
    source_id = "bavastro"
    label = "Bavastro"
    discovery_url = "https://api-parseo.bavastronline.com/auctions/"

    def scan(self) -> SourceScanResult:
        return self._scan_url(
            group_keys=("results", "auctions", "groups"),
            lot_keys=("lots", "items", "results", "data"),
            lot_url_key="lots_url",
        )


class CastellsSource(BaseAuctionSource):
    source_id = "castells"
    label = "Castells"
    discovery_url = "https://subastascastells.com/frontend.home.aspx"

    def scan(self) -> SourceScanResult:
        return self._scan_url(
            group_keys=("auctions", "groups", "remates", "results"),
            lot_keys=("lots", "items", "data"),
            lot_url_key="lots_url",
        )


class RemotesSource(BaseAuctionSource):
    source_id = "remotes"
    label = "Remotes"
    discovery_url = "https://www.remotes.com.uy/feed/publicados"

    def scan(self) -> SourceScanResult:
        return self._scan_url(
            group_keys=("auctions", "groups", "results", "items"),
            lot_keys=("lots", "items", "results", "data"),
        )


class TodoRematesSource(BaseAuctionSource):
    source_id = "todoremates"
    label = "TodoRemates"
    discovery_url = "https://todoremates.com/api/auctions"

    def scan(self) -> SourceScanResult:
        return self._scan_url(
            group_keys=("auctions", "groups", "terms", "results"),
            lot_keys=("lots", "products", "items", "data"),
        )


class PradoSource(BaseAuctionSource):
    source_id = "prado"
    label = "Prado Subastas"
    discovery_url = "https://pradorematesenlinea.uy/wp-json/wc/store/v1/products"

    def scan(self) -> SourceScanResult:
        return self._scan_url(
            group_keys=("auctions", "groups", "categories", "results"),
            lot_keys=("lots", "items", "results", "data"),
        )


__all__ = [
    "BavastroSource",
    "CastellsSource",
    "PradoSource",
    "RemotesSource",
    "TodoRematesSource",
]
