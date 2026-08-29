"""Bavastro's public JSON API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

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

API_BASE = "https://api-parseo.bavastronline.com/published_auctions"
LOTS_BASE = "https://api-parseo.bavastronline.com/auctions"
WEB_BASE = "https://www.bavastronline.com.uy"
PAGE_SIZE = 100
MAX_PAGES = 50


class BavastroSource(BaseAuctionSource):
    source_id = "bavastro"
    label = "Bavastro"
    discovery_url = f"{API_BASE}/"

    def _json(self, url: str) -> tuple[Any, Mapping[str, str]]:
        response = self.transport.get(url, timeout=self.timeout)
        return decode_response(response), response_headers(response)

    @staticmethod
    def _next(payload: Mapping[str, Any], page: int, url: str) -> str | None:
        declared_pages = payload.get("total_pages") or payload.get("page_count")
        if (
            declared_pages is not None
            and str(declared_pages).isdigit()
            and int(declared_pages) > MAX_PAGES
        ):
            raise ValueError("Bavastro declared page total is absurd")
        next_url = payload.get("next")
        if next_url:
            return absolute_url(url, next_url)
        results = payload.get("results")
        if isinstance(results, list) and len(results) >= PAGE_SIZE:
            return f"{API_BASE}/?page={page + 1}&limit={PAGE_SIZE}"
        return None

    def _discover(self) -> list[Mapping[str, Any]]:
        page = 1
        url: str | None = f"{API_BASE}/?page={page}&limit={PAGE_SIZE}"
        rows: list[Mapping[str, Any]] = []
        seen_urls: set[str] = set()
        while url:
            if url in seen_urls:
                raise ValueError("Bavastro discovery next cycle")
            if page > MAX_PAGES:
                raise ValueError("Bavastro discovery exceeded page limit")
            seen_urls.add(url)
            payload, _headers = self._json(url)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
                raise ValueError("Bavastro listing lacks results")
            rows.extend(item for item in payload["results"] if isinstance(item, Mapping))
            next_url = self._next(payload, page, url)
            page += 1
            url = next_url
        return rows

    def _detail(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_id = item.get("id")
        if not raw_id:
            raise ValueError("Bavastro auction lacks id")
        payload, _headers = self._json(f"{API_BASE}/{raw_id}/")
        if not isinstance(payload, Mapping):
            raise ValueError("Bavastro auction detail is not an object")
        return payload

    def _lots(self, auction_id: str) -> list[Mapping[str, Any]]:
        page = 1
        url: str | None = f"{LOTS_BASE}/{auction_id}/lots/published/?page={page}&page_size=50"
        rows: list[Mapping[str, Any]] = []
        seen_urls: set[str] = set()
        while url:
            if url in seen_urls:
                raise ValueError(f"Bavastro lots for {auction_id} next cycle")
            if page > MAX_PAGES:
                raise ValueError(f"Bavastro lots for {auction_id} exceeded page limit")
            seen_urls.add(url)
            payload, _headers = self._json(url)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
                raise ValueError(f"Bavastro lots for {auction_id} lack results")
            rows.extend(item for item in payload["results"] if isinstance(item, Mapping))
            next_url = payload.get("next")
            url = absolute_url(url, next_url) if next_url else None
            page += 1
        return rows

    def _parse_group(self, detail: Mapping[str, Any]) -> AuctionGroup:
        auction_id = clean_text(detail.get("id"))
        if not auction_id:
            raise ValueError("Bavastro auction lacks id")
        return AuctionGroup(
            source_id=self.source_id,
            auction_id=auction_id,
            title=clean_text(detail.get("name") or detail.get("title")) or f"Subasta {auction_id}",
            url=f"{WEB_BASE}/auctions/{auction_id}",
            category=clean_text(detail.get("category")),
            active=bool(detail.get("active", True)),
            closing_at=utc_datetime(detail.get("end_date")),
            observed_at=datetime.now(UTC),
        )

    def _parse_lot(self, raw: Mapping[str, Any], group: AuctionGroup) -> AuctionLot:
        nested_value = raw.get("lot")
        nested: Mapping[str, Any] = nested_value if isinstance(nested_value, Mapping) else {}
        lot_id = clean_text(raw.get("id"))
        description = clean_text(nested.get("description"))
        title = clean_text(nested.get("name") or nested.get("title")) or description
        if not lot_id or not title:
            raise ValueError("Bavastro lot lacks stable id or title")
        bids = raw.get("number_of_bids") or 0
        price = decimal_value(raw.get("best_price") if bids else raw.get("base_price"))
        currency_value = nested.get("currency")
        currency = clean_text(
            currency_value.get("prefix") if isinstance(currency_value, Mapping) else ""
        )
        auction_value = nested.get("auction")
        auction_end = auction_value.get("end_date") if isinstance(auction_value, Mapping) else None
        currency = "USD" if currency.upper() in {"USD", "US$"} else "UYU"
        return AuctionLot(
            source_id=self.source_id,
            auction_id=group.auction_id,
            lot_id=lot_id,
            title=title,
            description=description,
            category=group.category,
            price_value=price,
            price_currency=currency if price is not None else None,
            price_label=clean_text(raw.get("best_price") if bids else raw.get("base_price")),
            closing_at=utc_datetime(auction_end) or group.closing_at,
            lot_url=f"{WEB_BASE}/lot/{lot_id}",
            auction_url=group.url,
            image_url=first_image(nested.get("images"), base=WEB_BASE),
            active=True,
            observed_at=group.observed_at,
        )

    def scan(self) -> SourceScanResult:
        try:
            discovered = self._discover()
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Bavastro discovery failed ({type(exc).__name__})",),
            )
        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        errors: list[str] = []
        for item in discovered:
            try:
                detail = self._detail(item)
                if not bool(detail.get("active", False)) and clean_text(
                    detail.get("state")
                ).lower() not in {"active", "published", "open"}:
                    continue
                group = self._parse_group(detail)
                started = datetime.now(UTC)
                raw_lots = self._lots(group.auction_id)
                group_lots: list[AuctionLot] = []
                group_error_count = 0
                for raw in raw_lots:
                    try:
                        group_lots.append(self._parse_lot(raw, group))
                    except ValueError:
                        errors.append(f"Bavastro group {group.auction_id}: malformed lot")
                        group_error_count += 1
                groups.append(group)
                lots.extend(group_lots)
                status = "partial" if group_error_count else "complete"
                receipts.append(
                    GroupReceipt(
                        group_id=group.auction_id,
                        status=status,
                        inventory_authoritative=status == "complete",
                        lot_count=len(group_lots),
                        error_count=group_error_count,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:
                group_id = clean_text(item.get("id")) or "unknown"
                errors.append(f"Bavastro group {group_id}: {type(exc).__name__}")
                receipts.append(
                    GroupReceipt(
                        group_id=group_id,
                        status="failed",
                        inventory_authoritative=False,
                        lot_count=0,
                        error_count=1,
                        started_at=datetime.now(UTC),
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
