from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from auction_watch.sources import (
    DEFAULT_SOURCE_REGISTRY,
    BavastroSource,
    CastellsSource,
    PradoSource,
    RemotesSource,
    SourceRegistry,
    SourceSpec,
    TodoRematesSource,
)
from auction_watch.sources.base import BaseAuctionSource


class FakeTransport:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> Any:
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return self.payload


def _fixture(group_key: str, lot_key: str) -> dict[str, Any]:
    return {
        group_key: [
            {
                "id": "remate:2026/08",
                "title": "Subasta pública",
                "url": "https://example.test/auction/2026-08",
                "category": "videojuegos",
                "closing_at": "2026-08-30T18:00:00-03:00",
                lot_key: [
                    {
                        "id": "lote: 01/ñ",
                        "title": "Consola original",
                        "description": "Texto de fuente",
                        "url": "/lots/01",
                        "image": "/images/01.jpg",
                        "price": "1.234,56",
                        "currency": "uyu",
                        "closing_at": "2026-08-30T17:00:00Z",
                    }
                ],
            }
        ],
        "complete": True,
    }


@pytest.mark.parametrize(
    ("adapter", "group_key", "lot_key"),
    [
        (BavastroSource, "results", "lots"),
        (CastellsSource, "auctions", "items"),
        (RemotesSource, "groups", "lots"),
        (TodoRematesSource, "terms", "products"),
        (PradoSource, "categories", "items"),
    ],
)
def test_public_adapters_normalize_sanitized_fixtures(
    adapter: type[BaseAuctionSource], group_key: str, lot_key: str
) -> None:
    transport = FakeTransport(_fixture(group_key, lot_key))
    result = adapter(transport, timeout=3.5).scan()

    assert result.source_id == adapter.source_id
    assert result.discovery_status == "complete"
    assert result.inventory_authoritative is True
    assert len(result.groups) == len(result.lots) == len(result.receipts) == 1
    assert result.lots[0].lot_id == "lote: 01/ñ"
    assert result.lots[0].price_value == Decimal("1234.56")
    assert result.lots[0].price_currency == "UYU"
    assert result.lots[0].lot_url == "https://example.test/lots/01"
    assert result.lots[0].image_url == "https://example.test/images/01.jpg"
    assert result.lots[0].closing_at == datetime(2026, 8, 30, 17, tzinfo=UTC)
    assert result.receipts[0].inventory_authoritative is True
    assert transport.calls == [(adapter.discovery_url, 3.5)]


def test_multiple_groups_have_stable_external_identities() -> None:
    payload = {
        "groups": [
            {"id": "first:1", "title": "First", "url": "https://example.test/1", "lots": []},
            {"id": "second/2", "title": "Second", "url": "https://example.test/2", "lots": []},
        ],
        "complete": True,
    }
    result = RemotesSource(FakeTransport(payload)).scan()

    assert [group.auction_id for group in result.groups] == ["first:1", "second/2"]
    assert [receipt.status for receipt in result.receipts] == ["complete", "complete"]


def test_malformed_lot_isolated_and_marks_group_partial() -> None:
    payload = {
        "groups": [
            {
                "id": "auction-1",
                "title": "Auction",
                "url": "https://example.test/auction",
                "lots": [
                    {"id": "good", "title": "Good", "url": "/good"},
                    {"id": "bad", "title": "Missing URL"},
                ],
            }
        ],
        "complete": True,
    }
    result = RemotesSource(FakeTransport(payload)).scan()

    assert [lot.lot_id for lot in result.lots] == ["good"]
    assert result.discovery_status == "partial"
    assert result.inventory_authoritative is False
    assert result.receipts[0].status == "partial"
    assert result.receipts[0].error_count == 1


def test_empty_response_requires_structural_evidence() -> None:
    authoritative = RemotesSource(FakeTransport({"groups": [], "complete": True})).scan()
    suspicious = RemotesSource(FakeTransport({})).scan()

    assert authoritative.discovery_status == "complete"
    assert authoritative.inventory_authoritative is True
    assert authoritative.groups == ()
    assert suspicious.discovery_status == "failed"
    assert suspicious.inventory_authoritative is False


def test_transport_timeout_is_a_total_failed_scan() -> None:
    result = PradoSource(FakeTransport(error=TimeoutError())).scan()

    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False
    assert "TimeoutError" in result.errors[0]


def test_registry_is_typed_deterministic_and_rejects_duplicates() -> None:
    registry = SourceRegistry(
        (
            SourceSpec("remotes", "Remotes", RemotesSource),
            SourceSpec("prado", "Prado", PradoSource),
        )
    )

    assert [spec.source_id for spec in registry.specs()] == ["prado", "remotes"]
    assert [spec.source_id for spec in registry.select(("remotes", "prado"))] == [
        "remotes",
        "prado",
    ]
    assert [source.source_id for source in registry.build(FakeTransport())] == ["prado", "remotes"]
    with pytest.raises(ValueError, match="duplicate source_id"):
        registry.register(SourceSpec("prado", "Again", PradoSource))
    with pytest.raises(ValueError, match="unknown source_id"):
        registry.select(("missing",))


def test_default_registry_contains_only_public_source_adapters() -> None:
    assert [spec.source_id for spec in DEFAULT_SOURCE_REGISTRY.specs()] == [
        "bavastro",
        "castells",
        "prado",
        "remotes",
        "todoremates",
    ]


def test_source_module_does_not_require_profiles_or_matching() -> None:
    assert all(
        "profile" not in source.__class__.__module__
        and "matching" not in source.__class__.__module__
        for source in DEFAULT_SOURCE_REGISTRY.build(FakeTransport())
    )
