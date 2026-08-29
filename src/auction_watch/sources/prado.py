"""Prado Subastas adapter for the WooCommerce Store API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

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
from auction_watch.sources.transport import decode_response, response_headers

BASE_URL = "https://pradorematesenlinea.uy"
PRODUCTS_API_URL = f"{BASE_URL}/wp-json/wc/store/v1/products"
PAGE_SIZE = 100
MAX_PAGES = 50


def parse_price_markup(markup: str) -> tuple[Decimal | None, str]:
    status_match = re.search(r'data-status=["\']([^"\']+)', markup, re.IGNORECASE)
    status = status_match.group(1).lower() if status_match else "unknown"
    bid_match = re.search(r'data-bid=["\']([^"\']+)', markup, re.IGNORECASE)
    shown = re.search(r"(?:\$|UYU)\s*([0-9][0-9.,]*)", markup, re.IGNORECASE)
    return decimal_value(
        bid_match.group(1) if bid_match else shown.group(1) if shown else None
    ), status


class PradoSource(BaseAuctionSource):
    source_id = "prado"
    label = "Prado Subastas"
    discovery_url = PRODUCTS_API_URL

    def _pages(self) -> list[Mapping[str, Any]]:
        page = 1
        rows: list[Mapping[str, Any]] = []
        total: int | None = None
        seen_urls: set[str] = set()
        while True:
            if page > MAX_PAGES:
                raise ValueError("Prado pagination exceeded page limit")
            query = urlencode({"page": page, "per_page": PAGE_SIZE, "stock_status": "instock"})
            url = f"{PRODUCTS_API_URL}?{query}"
            if url in seen_urls:
                raise ValueError("Prado pagination next cycle")
            seen_urls.add(url)
            response = self.transport.get(url, timeout=self.timeout)
            payload = decode_response(response)
            if not isinstance(payload, list):
                raise ValueError("Prado products response is not a JSON array")
            rows.extend(item for item in payload if isinstance(item, Mapping))
            try:
                declared_total = int(response_headers(response).get("X-WP-TotalPages") or 0) or None
                if declared_total is not None and declared_total > MAX_PAGES:
                    raise ValueError("Prado declared page total is absurd")
                total = declared_total or total
            except (TypeError, ValueError):
                pass
            if total is not None and page >= total:
                return rows
            if total is None and len(payload) < PAGE_SIZE:
                return rows
            page += 1

    def scan(self) -> SourceScanResult:
        try:
            products = self._pages()
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Prado product discovery failed ({type(exc).__name__})",),
            )
        started = datetime.now(UTC)
        grouped: dict[str, tuple[AuctionGroup, list[AuctionLot]]] = {}
        errors: list[str] = []
        for product in products:
            if (
                clean_text(product.get("type")).lower() != "auction"
                or not product.get("id")
                or product.get("is_password_protected") is True
                or product.get("is_in_stock") is False
            ):
                continue
            categories_value = product.get("categories")
            categories = categories_value if isinstance(categories_value, list) else []
            category: Mapping[str, Any] = {}
            for item in categories:
                if isinstance(item, Mapping):
                    category = item
                    break
            group_id = clean_text(category.get("id")) or "auctions"
            group_title = clean_text(category.get("name")) or "Subastas online"
            group_url = absolute_url(BASE_URL, category.get("link") or BASE_URL)
            price, status = parse_price_markup(str(product.get("price_html") or ""))
            if status in {"expired", "closed", "ended", "finished"}:
                continue
            if group_id not in grouped:
                grouped[group_id] = (
                    AuctionGroup(
                        source_id=self.source_id,
                        auction_id=group_id,
                        title=group_title,
                        url=group_url,
                        category=group_title,
                        active=True,
                        closing_at=None,
                        observed_at=started,
                    ),
                    [],
                )
            group, group_lots = grouped[group_id]
            try:
                lot_id = clean_text(product.get("id"))
                title = clean_text(product.get("name"))
                lot_url = clean_text(product.get("permalink"))
                if not lot_id or not title or not lot_url:
                    raise ValueError("product lacks stable id, name, or permalink")
                group_lots.append(
                    AuctionLot(
                        source_id=self.source_id,
                        auction_id=group_id,
                        lot_id=lot_id,
                        title=title,
                        description=clean_text(product.get("short_description")),
                        category=group_title,
                        price_value=price,
                        price_currency="UYU" if price is not None else None,
                        price_label=clean_text(product.get("price_html")),
                        closing_at=utc_datetime(product.get("closing_at")),
                        lot_url=absolute_url(BASE_URL, lot_url),
                        auction_url=group.url,
                        image_url=first_image(product.get("images"), base=BASE_URL),
                        active=status not in {"expired", "closed", "ended", "finished"},
                        observed_at=started,
                    )
                )
            except (TypeError, ValueError):
                errors.append(f"Prado group {group_id}: malformed product")
        receipts = tuple(
            GroupReceipt(
                group_id=group_id,
                status="partial" if any(group_id in error for error in errors) else "complete",
                inventory_authoritative=not any(group_id in error for error in errors),
                lot_count=len(group_lots),
                error_count=1 if any(group_id in error for error in errors) else 0,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            for group_id, (_group, group_lots) in grouped.items()
        )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(group for group, _lots in grouped.values()),
            lots=tuple(lot for _group, group_lots in grouped.values() for lot in group_lots),
            discovery_status="complete" if not errors else "partial",
            inventory_authoritative=not errors,
            receipts=receipts,
            errors=tuple(errors),
        )
