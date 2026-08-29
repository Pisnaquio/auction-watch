"""TodoRemates adapter for its WordPress taxonomy and WooCommerce APIs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.parsing import (
    absolute_url,
    clean_text,
    decimal_value,
    first_image,
    utc_datetime,
)
from auction_watch.sources.transport import (
    HttpxTransport,
    Transport,
    decode_response,
    response_headers,
)

BASE_URL = "https://todoremates.com.uy"
REMATES_API_URL = f"{BASE_URL}/wp-json/wp/v2/remate"
PRODUCTS_API_URL = f"{BASE_URL}/wp-json/wc/store/v1/products"
PAGE_SIZE = 100
MAX_PAGES = 50
SOURCE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Auction Watch/0.1 (+public source adapter; read-only; todoremates)",
}


def _error_label(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class TodoRematesSource(BaseAuctionSource):
    source_id = "todoremates"
    label = "TodoRemates"
    discovery_url = REMATES_API_URL

    def __init__(self, transport: Transport, *, timeout: float | None = None) -> None:
        super().__init__(transport, timeout=timeout)
        if isinstance(transport, HttpxTransport):
            transport.set_headers(SOURCE_HEADERS)

    def _page(
        self, url: str, page: int, **params: object
    ) -> tuple[list[Mapping[str, Any]], int | None]:
        query = {"page": page, "per_page": PAGE_SIZE, **params}
        response = self.transport.get(f"{url}?{urlencode(query)}", timeout=self.timeout)
        payload = decode_response(response)
        if not isinstance(payload, list):
            raise ValueError("TodoRemates response is not a JSON array")
        headers = response_headers(response)
        try:
            total = int(headers.get("X-WP-TotalPages") or 0) or None
        except (TypeError, ValueError):
            total = None
        return [item for item in payload if isinstance(item, Mapping)], total

    def _pages(self, url: str, **params: object) -> list[Mapping[str, Any]]:
        page = 1
        rows: list[Mapping[str, Any]] = []
        total: int | None = None
        seen_urls: set[str] = set()
        while True:
            if page > MAX_PAGES:
                raise ValueError("TodoRemates pagination exceeded page limit")
            request_url = f"{url}?{urlencode({'page': page, 'per_page': PAGE_SIZE, **params})}"
            if request_url in seen_urls:
                raise ValueError("TodoRemates pagination next cycle")
            seen_urls.add(request_url)
            current, declared_total = self._page(url, page, **params)
            total = declared_total or total
            if total is not None and total > MAX_PAGES:
                raise ValueError("TodoRemates declared page total is absurd")
            rows.extend(current)
            if total is not None:
                if page >= total:
                    return rows
            elif len(current) < PAGE_SIZE:
                return rows
            page += 1

    def _product_lot(self, product: Mapping[str, Any], group: AuctionGroup) -> AuctionLot:
        lot_id = clean_text(product.get("id"))
        title = clean_text(product.get("name"))
        lot_url = clean_text(product.get("permalink"))
        if not lot_id or not title or not lot_url:
            raise ValueError("TodoRemates product lacks id, name, or permalink")
        prices_value = product.get("prices")
        prices: Mapping[str, Any] = prices_value if isinstance(prices_value, Mapping) else {}
        price = decimal_value(prices.get("price"))
        minor_unit = (
            int(prices.get("currency_minor_unit") or 0)
            if str(prices.get("currency_minor_unit") or "0").isdigit()
            else 0
        )
        if price is not None and minor_unit:
            price /= 10**minor_unit
        currency = clean_text(prices.get("currency_code") or "UYU").upper()
        return AuctionLot(
            source_id=self.source_id,
            auction_id=group.auction_id,
            lot_id=lot_id,
            title=title,
            description=clean_text(product.get("description") or product.get("short_description")),
            category=group.category,
            price_value=price,
            price_currency=currency if price is not None else None,
            price_label=clean_text(prices.get("price")),
            closing_at=utc_datetime(product.get("closing_at")) or group.closing_at,
            lot_url=absolute_url(BASE_URL, lot_url),
            auction_url=group.url,
            image_url=first_image(product.get("images"), base=BASE_URL),
            active=product.get("is_password_protected") is not True
            and product.get("is_in_stock") is not False,
            observed_at=group.observed_at,
        )

    def scan(self) -> SourceScanResult:
        try:
            terms = self._pages(REMATES_API_URL, hide_empty="true")
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"TodoRemates taxonomy failed ({_error_label(exc)})",),
            )
        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        errors: list[str] = []
        for term in terms:
            group_id = clean_text(term.get("id"))
            if not group_id:
                errors.append("TodoRemates taxonomy term lacks id")
                continue
            started = datetime.now(UTC)
            group = AuctionGroup(
                source_id=self.source_id,
                auction_id=group_id,
                title=clean_text(term.get("name")) or f"Remate {group_id}",
                url=absolute_url(BASE_URL, term.get("link") or BASE_URL),
                category=clean_text(term.get("name")),
                active=True,
                closing_at=None,
                observed_at=started,
            )
            groups.append(group)
            try:
                products = self._pages(PRODUCTS_API_URL, _unstable_tax_remate=group_id)
            except Exception as exc:
                errors.append(
                    "TodoRemates group "
                    f"{group_id}: product pagination failed ({_error_label(exc)})"
                )
                receipts.append(
                    GroupReceipt(
                        group_id=group_id,
                        status="failed",
                        lot_count=0,
                        error_count=1,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
                continue
            group_lots: list[AuctionLot] = []
            group_error_count = 0
            for product in products:
                try:
                    if (
                        not product.get("id")
                        or product.get("is_password_protected") is True
                        or product.get("is_in_stock") is False
                    ):
                        continue
                    group_lots.append(self._product_lot(product, group))
                except (TypeError, ValueError):
                    errors.append(f"TodoRemates group {group_id}: malformed product")
                    group_error_count += 1
            lots.extend(group_lots)
            partial = group_error_count > 0
            receipts.append(
                GroupReceipt(
                    group_id=group_id,
                    status="partial" if partial else "complete",
                    inventory_authoritative=not partial,
                    lot_count=len(group_lots),
                    error_count=group_error_count,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(groups),
            lots=tuple(lots),
            discovery_status="complete" if not errors else "partial",
            inventory_authoritative=not errors,
            receipts=tuple(receipts),
            errors=tuple(errors),
        )
