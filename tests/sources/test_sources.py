from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
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
from auction_watch.sources.bavastro import API_BASE, LOTS_BASE
from auction_watch.sources.castells import LOTS_URL
from auction_watch.sources.prado import PRODUCTS_API_URL as PRADO_PRODUCTS_URL
from auction_watch.sources.todoremates import PRODUCTS_API_URL, REMATES_API_URL
from auction_watch.sources.transport import HttpxTransport

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
        self.request_headers: list[Mapping[str, str]] = []

    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
    ) -> FakeResponse:
        self.calls.append((url, timeout))
        self.request_headers.append(dict(headers or {}))
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
        if url.startswith(f"{LOTS_BASE}/123/lots/published") and "page=1" in url:
            return FakeResponse(payload=page1)
        if url.startswith(f"{LOTS_BASE}/123/lots/published") and "page=2" in url:
            return FakeResponse(payload=page2)
        if url == f"{API_BASE}/124/":
            return FakeResponse(payload={"id": 124, "name": "Fallida", "active": True})
        if url.startswith(f"{LOTS_BASE}/124/lots/published"):
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
    assert all(f"{API_BASE}/" not in url for url, _timeout in transport.calls if "/lots/" in url)


def test_bavastro_lots_do_not_use_discovery_base() -> None:
    transport = FakeTransport(
        lambda url: (
            FakeResponse(payload={"results": [], "next": None})
            if url.startswith(f"{API_BASE}/?page=1")
            else (_ for _ in ()).throw(AssertionError(url))
        )
    )
    result = BavastroSource(transport).scan()
    assert result.discovery_status == "complete"
    assert not any("/published_auctions/" in url and "/lots/" in url for url, _ in transport.calls)


def test_bavastro_next_cycle_fails_closed() -> None:
    url = f"{API_BASE}/?page=1&limit=100"
    result = BavastroSource(
        FakeTransport(lambda _url: FakeResponse(payload={"results": [], "next": url}))
    ).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False


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


def test_castells_normalizes_observed_currency_labels_and_isolates_unknowns() -> None:
    def handler(url: str) -> FakeResponse:
        if url == "https://subastascastells.com/frontend.home.aspx":
            return FakeResponse(text=load_text("castells_home.html"))
        if url.startswith(LOTS_URL):
            return FakeResponse(payload=load_json("castells_lots_currencies.json"))
        raise AssertionError(url)

    result = CastellsSource(FakeTransport(handler)).scan()
    assert [lot.price_currency for lot in result.lots] == [
        "UYU",
        "UYU",
        "UYU",
        "USD",
        "USD",
        "USD",
        None,
    ]
    assert result.lots[-1].price_label == "500"
    assert result.lots[-1].price_value is None
    assert result.receipts[0].status == "partial"
    assert result.receipts[0].error_count == 1
    assert result.inventory_authoritative is False


def test_castells_request_budget_fails_pending_groups_closed() -> None:
    document = "<html><script>GXState=" + "".join(
        f'{{"RemateImagen":"/img.jpg","RemateId":{id_},"RemateNombre":"Remate {id_}"}}'
        for id_ in (1, 2, 3)
    ) + ";</script></html>"

    def handler(url: str) -> FakeResponse:
        if url == "https://subastascastells.com/frontend.home.aspx":
            return FakeResponse(text=document)
        if url.startswith(LOTS_URL):
            return FakeResponse(payload={"data": []})
        raise AssertionError(url)

    transport = FakeTransport(handler)
    result = CastellsSource(transport, max_requests=2).scan()
    assert len(result.receipts) == 3
    assert sum(receipt.status == "failed" for receipt in result.receipts) >= 1
    assert result.inventory_authoritative is False
    assert len(transport.calls) == 2


def test_castells_deadline_before_discovery_makes_no_request() -> None:
    clock_values = iter((0.0, 1.0))
    transport = FakeTransport(lambda _url: FakeResponse(text="unused"))
    result = CastellsSource(
        transport,
        deadline_seconds=0.5,
        clock=lambda: next(clock_values),
    ).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False
    assert "deadline exceeded" in result.errors[0]
    assert transport.calls == []


def test_castells_deadline_preserves_finished_groups_and_fails_pending_in_order() -> None:
    document = "<html><script>GXState=" + "".join(
        f'{{"RemateImagen":"/img.jpg","RemateId":{id_},"RemateNombre":"Remate {id_}"}}'
        for id_ in (3, 1, 2)
    ) + ";</script></html>"
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls <= 4:
            return 0.0
        return 2.0

    def handler(url: str) -> FakeResponse:
        if url == "https://subastascastells.com/frontend.home.aspx":
            return FakeResponse(text=document)
        return FakeResponse(payload={"data": []})

    transport = FakeTransport(handler)
    result = CastellsSource(
        transport,
        timeout=5,
        deadline_seconds=1.0,
        clock=clock,
    ).scan()
    assert len(result.groups) == 2
    assert [group.auction_id for group in result.groups] == sorted(
        group.auction_id for group in result.groups
    )
    assert [receipt.group_id for receipt in result.receipts] == ["1", "2", "3"]
    assert [receipt.status for receipt in result.receipts].count("complete") == 2
    assert [receipt.status for receipt in result.receipts].count("failed") == 1
    assert all(timeout <= 1.0 for _url, timeout in transport.calls)
    assert result.inventory_authoritative is False


def test_castells_productive_deadline_is_bounded_and_order_is_deterministic() -> None:
    document = "<html><script>GXState=" + "".join(
        f'{{"RemateImagen":"/img.jpg","RemateId":{id_},"RemateNombre":"Remate {id_}"}}'
        for id_ in (20, 10, 30)
    ) + ";</script></html>"

    def handler(url: str) -> FakeResponse:
        if url == "https://subastascastells.com/frontend.home.aspx":
            return FakeResponse(text=document)
        return FakeResponse(payload={"data": []})

    transport = FakeTransport(handler)
    started = monotonic()
    result = CastellsSource(transport, deadline_seconds=1.0).scan()
    assert monotonic() - started < 1.0
    assert [group.auction_id for group in result.groups] == ["10", "20", "30"]
    assert [receipt.group_id for receipt in result.receipts] == ["10", "20", "30"]
    assert result.inventory_authoritative is True


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


def test_todoremates_403_is_sanitized_and_not_empty_inventory() -> None:
    request = httpx.Request("GET", REMATES_API_URL)
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)
    result = TodoRematesSource(FakeTransport(lambda _url: (_ for _ in ()).throw(error))).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False
    assert result.errors == ("TodoRemates taxonomy failed (HTTP 403)",)


def test_todoremates_uses_source_specific_public_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)
    base_headers = dict(client.headers)
    TodoRematesSource(transport)._page(REMATES_API_URL, 1)
    assert dict(client.headers) == base_headers
    assert requests[0].headers["accept"] == "application/json"
    assert "todoremates" in requests[0].headers["user-agent"]
    client.close()


def test_source_headers_are_isolated_in_both_construction_orders() -> None:
    source_types = (BavastroSource, CastellsSource, RemotesSource, TodoRematesSource, PradoSource)
    for ordered_types in (source_types, tuple(reversed(source_types))):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request, captured=requests) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, request=request, json=[])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        transport = HttpxTransport(client)
        base_accept = client.headers["accept"]
        base_user_agent = client.headers["user-agent"]
        base_headers = dict(client.headers)
        instances = [source_type(transport) for source_type in ordered_types]
        assert dict(client.headers) == base_headers
        todo = next(source for source in instances if isinstance(source, TodoRematesSource))
        bavastro = next(source for source in instances if isinstance(source, BavastroSource))
        todo._page(REMATES_API_URL, 1)
        bavastro._json("https://api-parseo.bavastronline.com/public")
        assert requests[0].headers["accept"] == "application/json"
        assert "todoremates" in requests[0].headers["user-agent"]
        assert requests[1].headers["accept"] == base_accept
        assert requests[1].headers["user-agent"] == base_user_agent
        client.close()


def test_source_headers_remain_isolated_concurrently() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)
    todo = TodoRematesSource(transport)
    bavastro = BavastroSource(transport)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(todo._page, REMATES_API_URL, index)
            for index in range(1, 6)
        ] + [
            executor.submit(bavastro._json, f"https://example.test/{index}")
            for index in range(1, 6)
        ]
        for future in futures:
            future.result()
    todo_requests = [request for request in requests if "todoremates.com.uy" in str(request.url)]
    bavastro_requests = [request for request in requests if "example.test" in str(request.url)]
    assert len(todo_requests) == len(bavastro_requests) == 5
    assert all(request.headers["accept"] == "application/json" for request in todo_requests)
    assert all("todoremates" in request.headers["user-agent"] for request in todo_requests)
    assert all(
        "application/json, text/html" in request.headers["accept"]
        for request in bavastro_requests
    )
    assert all("todoremates" not in request.headers["user-agent"] for request in bavastro_requests)
    client.close()


def test_wordpress_absurd_page_total_is_rejected() -> None:
    response = FakeResponse(
        payload=load_json("todoremates_terms.json"), headers={"X-WP-TotalPages": "51"}
    )
    result = TodoRematesSource(FakeTransport(lambda _url: response)).scan()
    assert result.discovery_status == "failed"
    assert result.inventory_authoritative is False


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


def test_transport_sets_public_headers_and_retries_transient_status_once() -> None:
    statuses = iter((503, 200))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(statuses), request=request, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client)
    response = transport.get("https://example.test/public", timeout=2)
    assert response.status_code == 200
    assert len(requests) == 2
    assert "Auction Watch/0.1" in requests[0].headers["user-agent"]
    assert "application/json" in requests[0].headers["accept"]
    client.close()


def test_transport_retry_respects_absolute_deadline() -> None:
    requests: list[httpx.Request] = []
    clock_values = iter((0.0, 1.1))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpxTransport(client, clock=lambda: next(clock_values))
    with pytest.raises(RuntimeError, match="deadline"):
        transport.get("https://example.test/public", timeout=5, deadline=1.0)
    assert len(requests) == 1
    client.close()


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
