from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
from auction_watch.sources.bavastro import API_BASE
from auction_watch.sources.castells import LOTS_URL
from auction_watch.sources.prado import PRODUCTS_API_URL as PRADO_PRODUCTS_URL
from auction_watch.sources.todoremates import PRODUCTS_API_URL, REMATES_API_URL

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(
        self, *, payload: Any = None, text: str | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if self.text is not None:
            raise ValueError("not JSON")
        return self.payload


class FakeTransport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        return self.handler(url)


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_bavastro_real_json_api_covers_lot_pagination() -> None:
    discovery = load_json("bavastro_discovery.json")
    page1 = load_json("bavastro_lots_page1.json")
    page2 = load_json("bavastro_lots_page2.json")

    def handler(url: str) -> FakeResponse:
        if url.startswith(f"{API_BASE}/?page=1"):
            return FakeResponse(payload=discovery)
        if url == f"{API_BASE}/123/":
            return FakeResponse(payload=load_json("bavastro_detail.json"))
        if "123/lots/published" in url and "page=1" in url:
            return FakeResponse(payload=page1)
        if "123/lots/published" in url and "page=2" in url:
            return FakeResponse(payload=page2)
        if url == f"{API_BASE}/124/":
            return FakeResponse(payload={"id": 124, "name": "Fallida", "active": True})
        if "124/lots/published" in url:
            raise TimeoutError("fixture timeout")
        raise AssertionError(url)

    transport = FakeTransport(handler)
    result = BavastroSource(transport, timeout=4).scan()

    assert result.source_id == "bavastro"
    assert result.discovery_status == "partial"
    assert result.inventory_authoritative is False
    assert [lot.lot_id for lot in result.lots] == ["9001", "9002"]
    assert result.lots[0].price_value == Decimal("1200.00")
    assert result.receipts[0].status == "complete"
    assert result.receipts[1].status == "failed"
    assert all(timeout == 4 for _url, timeout in transport.calls)


def test_castells_requires_html_gxstate_and_reads_lots_endpoint() -> None:
    def handler(url: str) -> FakeResponse:
        if url == "https://subastascastells.com/frontend.home.aspx":
            return FakeResponse(text=load_text("castells_home.html"))
        if url.startswith(LOTS_URL):
            return FakeResponse(payload=load_json("castells_lots.json"))
        raise AssertionError(url)

    result = CastellsSource(FakeTransport(handler)).scan()
    assert result.discovery_status == "complete"
    assert result.groups[0].auction_id == "77"
    assert result.lots[0].lot_id == "77:16"
    assert str(result.lots[0].price_value) == "1234.50"


def test_castells_json_is_rejected_instead_of_coerced() -> None:
    result = CastellsSource(
        FakeTransport(lambda _url: FakeResponse(payload={"results": []}))
    ).scan()
    assert result.discovery_status == "failed"
    assert "Castells" in result.errors[0]


def test_remotes_parses_rss_and_deduplicates_by_query_lot_id() -> None:
    result = RemotesSource(
        FakeTransport(lambda url: FakeResponse(text=load_text("remotes_feed.xml")))
    ).scan()
    assert result.discovery_status == "complete"
    assert [group.auction_id for group in result.groups] == ["7544"]
    assert [lot.lot_id for lot in result.lots] == ["16", "18"]
    assert result.lots[1].image_url == "https://www.remotes.com.uy/media/b.jpg"


def test_remotes_json_is_rejected_and_missing_lot_id_is_partial() -> None:
    result = RemotesSource(FakeTransport(lambda _url: FakeResponse(payload=[]))).scan()
    assert result.discovery_status == "failed"
    malformed = (
        "<rss><channel><item><title>A</title>"
        "<link>https://www.remotes.com.uy/participar/remate/1</link>"
        "<cantLotes>1</cantLotes><lotes><lote><title>Sin ID</title>"
        "</lote></lotes></item></channel></rss>"
    )
    result = RemotesSource(FakeTransport(lambda _url: FakeResponse(text=malformed))).scan()
    assert result.discovery_status == "partial"
    assert result.inventory_authoritative is False


def test_todoremates_uses_real_wordpress_and_woocommerce_pagination() -> None:
    terms = load_json("todoremates_terms.json")
    products = load_json("todoremates_products.json")

    def handler(url: str) -> FakeResponse:
        if url.startswith(f"{REMATES_API_URL}?"):
            assert parse_qs(urlsplit(url).query)["hide_empty"] == ["true"]
            return FakeResponse(payload=terms, headers={"X-WP-TotalPages": "1"})
        if url.startswith(f"{PRODUCTS_API_URL}?"):
            query = parse_qs(urlsplit(url).query)
            assert query["_unstable_tax_remate"] == ["39"]
            return FakeResponse(payload=products, headers={"X-WP-TotalPages": "1"})
        raise AssertionError(url)

    transport = FakeTransport(handler)
    result = TodoRematesSource(transport).scan()
    assert result.discovery_status == "complete"
    assert result.groups[0].auction_id == "39"
    assert result.lots[0].price_currency == "USD"
    assert all("todoremates.com.uy/wp-json/" in url for url, _timeout in transport.calls)


def test_todoremates_missing_second_page_is_not_authoritative() -> None:
    calls = 0

    def handler(url: str) -> FakeResponse:
        nonlocal calls
        calls += 1
        if "wp/v2/remate" in url and parse_qs(urlsplit(url).query).get("page") == ["1"]:
            return FakeResponse(
                payload=load_json("todoremates_terms.json"), headers={"X-WP-TotalPages": "2"}
            )
        raise TimeoutError("page two unavailable")

    result = TodoRematesSource(FakeTransport(handler)).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False
    assert calls == 2


def test_prado_uses_woocommerce_products_and_filters_non_auctions() -> None:
    transport = FakeTransport(
        lambda url: FakeResponse(
            payload=load_json("prado_products.json"), headers={"X-WP-TotalPages": "1"}
        )
    )
    result = PradoSource(transport).scan()
    assert result.discovery_status == "complete"
    assert len(result.groups) == len(result.lots) == 1
    assert result.lots[0].lot_id == "272662"
    assert result.lots[0].price_value == 1000
    assert transport.calls[0][0].startswith(f"{PRADO_PRODUCTS_URL}?")


def test_prado_structural_marker_drift_fails_closed() -> None:
    result = PradoSource(FakeTransport(lambda _url: FakeResponse(payload={"products": []}))).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False


def test_registry_selection_and_duplicate_detection_remain_deterministic() -> None:
    registry = SourceRegistry(
        (SourceSpec("remotes", "Remotes", RemotesSource), SourceSpec("prado", "Prado", PradoSource))
    )
    assert [spec.source_id for spec in registry.specs()] == ["prado", "remotes"]
    assert [spec.source_id for spec in registry.select(("remotes", "prado"))] == [
        "remotes",
        "prado",
    ]
    with pytest.raises(ValueError, match="duplicate source_id"):
        registry.register(SourceSpec("prado", "Again", PradoSource))
    assert {spec.source_id for spec in DEFAULT_SOURCE_REGISTRY.specs()} == {
        "bavastro",
        "castells",
        "remotes",
        "todoremates",
        "prado",
    }
