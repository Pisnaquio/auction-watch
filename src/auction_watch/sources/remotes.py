"""Remotes RSS adapter with strict XML structure and stable query IDs."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

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
from auction_watch.sources.transport import Transport, decode_response

BASE_URL = "https://www.remotes.com.uy"
FEED_URL = f"{BASE_URL}/feed/publicados"
MAX_PAGES = 1
# The feed carries no price, so each auction page is read for its JSON-LD
# offers. Remotes answers 429 after three quick requests and keeps answering it
# under a two-second pace; one request every three seconds held. So the
# enrichment is sequential, paced, and spent on the auctions closing soonest —
# the ones whose price informs a decision today. The rest are priced by a later
# run, as their closing comes near.
PRICE_PAUSE_SECONDS = 3.0
MAX_PRICE_REQUESTS = 12
MAX_PRICE_FAILURES = 2
MAX_PRICE_SECONDS = 90.0
PRICE_HORIZON = timedelta(days=2)

_LD_JSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _tag(node: ElementTree.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ElementTree.Element, name: str) -> str:
    for child in node:
        if _tag(child) == name.lower():
            return clean_text(child.text)
    return ""


def _group_id(url: str) -> str:
    match = re.search(r"/remate/([^/?#]+)", url)
    return match.group(1) if match else url


def _lot_id(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    return query.get("lote", [""])[0].strip()


def _feed_time(value: str) -> datetime | None:
    if value.isdigit():
        return datetime.fromtimestamp(int(value), tz=UTC)
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return utc_datetime(value)


def _group_closing(raw: Mapping[str, Any]) -> datetime | None:
    """When the auction closes, from whichever field this feed carries.

    The feed has never shipped a ``<cierre>`` element: every item announces its
    moment as ``<fecha>``, a Unix timestamp, and the feed drops an auction once
    that moment passes. Reading only ``<cierre>`` left every Remotes lot without
    a closing time, which hid the one fact that orders a watch list.
    """

    for key in ("closing_at", "event_at"):
        parsed = _feed_time(clean_text(raw.get(key)))
        if parsed is not None:
            return parsed
    return None


def _format_amount(value: Decimal) -> str:
    integral = value == value.to_integral_value()
    raw = f"{value:,.0f}" if integral else f"{value:,.2f}"
    return raw.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _price_label(value: Decimal, currency: str) -> str:
    amount = _format_amount(value)
    return f"$ {amount}" if currency == "UYU" else f"{currency} {amount}"


def _ld_products(payload: Any) -> Iterator[Mapping[str, Any]]:
    """Yield every Product node of a JSON-LD document, whatever nests it."""

    if isinstance(payload, list):
        for entry in payload:
            yield from _ld_products(entry)
        return
    if not isinstance(payload, Mapping):
        return
    if payload.get("@type") == "Product":
        yield payload
    for key in ("@graph", "itemListElement", "item", "mainEntity"):
        if key in payload:
            yield from _ld_products(payload[key])


def parse_lot_prices(document: str) -> dict[str, tuple[Decimal, str]]:
    """Read an auction page's JSON-LD offers, keyed by the lot's query id.

    This is the only place Remotes publishes a price. A lot nobody has bid on
    carries no offer, and stays priceless rather than being shown as free.
    """

    prices: dict[str, tuple[Decimal, str]] = {}
    for block in _LD_JSON.findall(document):
        try:
            payload = json.loads(block)
        except ValueError:
            continue
        for product in _ld_products(payload):
            offer = product.get("offers")
            if not isinstance(offer, Mapping):
                continue
            value = decimal_value(offer.get("price"))
            currency = clean_text(offer.get("priceCurrency")).upper()
            if value is None or value <= 0 or not currency:
                continue
            lot_id = _lot_id(clean_text(offer.get("url")) or clean_text(product.get("url")))
            if not lot_id:
                lot_id = _sku_lot_id(clean_text(product.get("sku")))
            if lot_id:
                prices[lot_id] = (value, currency)
    return prices


def _sku_lot_id(sku: str) -> str:
    """Recover the lot id from ``LOTE-<remate>-<lote>`` when the offer has no URL."""

    parts = sku.split("-", 2)
    return parts[2].strip() if len(parts) == 3 and parts[0].upper() == "LOTE" else ""


def parse_rss(document: str) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    if "<rss" not in document.lower() or "<channel" not in document.lower():
        raise ValueError("Remotes response lacks RSS channel")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ValueError("Remotes RSS is malformed") from exc
    channel = next((node for node in root.iter() if _tag(node) == "channel"), None)
    if channel is None:
        raise ValueError("Remotes RSS lacks channel")
    groups: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for item in (node for node in channel if _tag(node) == "item"):
        raw_url = _child_text(item, "link")
        url = absolute_url(BASE_URL, raw_url)
        group_id = _group_id(url)
        if not raw_url or group_id == BASE_URL:
            errors.append("Remotes item lacks stable remate URL")
            continue
        declared = _child_text(item, "cantlotes")
        lots: list[Mapping[str, Any]] = []
        parent = next((node for node in item if _tag(node) == "lotes"), None)
        if parent is not None:
            seen_lot_ids: set[str] = set()
            for lot_node in (node for node in parent if _tag(node) == "lote"):
                lot_url = absolute_url(url, _child_text(lot_node, "link"))
                lot_id = _lot_id(lot_url)
                if not lot_id:
                    errors.append(f"Remotes group {group_id}: lot lacks lote query id")
                    continue
                if lot_id in seen_lot_ids:
                    continue
                seen_lot_ids.add(lot_id)
                lots.append(
                    {
                        "id": lot_id,
                        "title": _child_text(lot_node, "title"),
                        "description": _child_text(lot_node, "description"),
                        "url": lot_url,
                        "image": _child_text(lot_node, "foto"),
                    }
                )
        groups.append(
            {
                "id": group_id,
                "title": _child_text(item, "title"),
                "url": url,
                "closing_at": _child_text(item, "cierre"),
                "event_at": _child_text(item, "fecha"),
                "declared_lots": declared,
                "lots": lots,
            }
        )
    return tuple(groups), tuple(errors)


class RemotesSource(BaseAuctionSource):
    source_id = "remotes"
    label = "Remotes"
    discovery_url = FEED_URL

    def __init__(
        self,
        transport: Transport,
        *,
        timeout: float | None = None,
        ignored_titles: tuple[str, ...] = (),
        max_price_requests: int = MAX_PRICE_REQUESTS,
        price_pause_seconds: float = PRICE_PAUSE_SECONDS,
        price_deadline_seconds: float = MAX_PRICE_SECONDS,
        price_horizon: timedelta = PRICE_HORIZON,
        max_price_failures: int = MAX_PRICE_FAILURES,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(transport, timeout=timeout, ignored_titles=ignored_titles)
        if max_price_requests < 0:
            raise ValueError("Remotes max_price_requests must not be negative")
        if price_pause_seconds < 0:
            raise ValueError("Remotes price_pause_seconds must not be negative")
        if price_deadline_seconds <= 0:
            raise ValueError("Remotes price_deadline_seconds must be positive")
        if max_price_failures < 1:
            raise ValueError("Remotes max_price_failures must be positive")
        self.max_price_requests = max_price_requests
        self.price_pause_seconds = price_pause_seconds
        self.price_deadline_seconds = price_deadline_seconds
        self.price_horizon = price_horizon
        self.max_price_failures = max_price_failures
        self.clock = clock
        self.sleep = sleep

    def _priced_first(self, groups: Sequence[AuctionGroup]) -> list[AuctionGroup]:
        """The auctions worth a request today: the ones closing soonest."""

        horizon = datetime.now(UTC) + self.price_horizon
        near = [
            group
            for group in groups
            if group.closing_at is None or group.closing_at <= horizon
        ]
        near.sort(key=lambda group: (group.closing_at is None, group.closing_at or datetime.max))
        return near[: self.max_price_requests]

    def _lot_prices(
        self, groups: Sequence[AuctionGroup]
    ) -> tuple[dict[str, dict[str, tuple[Decimal, str]]], list[str]]:
        """Read the auction pages one at a time, paced, for their prices.

        A page that fails costs the price of that auction and nothing else: the
        lots came from the feed and are still complete, so this never turns a
        healthy scan into a partial one. Repeated refusals stop the enrichment
        instead of pushing a source that is already saying no.
        """

        prices: dict[str, dict[str, tuple[Decimal, str]]] = {}
        warnings: list[str] = []
        selected = self._priced_first(groups)
        if not selected:
            return prices, warnings
        deadline = self.clock() + self.price_deadline_seconds
        failures = 0
        for index, group in enumerate(selected):
            if index and self.price_pause_seconds > 0:
                if deadline - self.clock() <= self.price_pause_seconds:
                    warnings.append("Remotes: price lookup ran out of time")
                    break
                self.sleep(self.price_pause_seconds)
            try:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError("Remotes price deadline exceeded")
                document = decode_response(
                    self.transport.get(
                        group.url,
                        timeout=min(self.timeout, remaining),
                        deadline=deadline,
                    )
                )
                if not isinstance(document, str):
                    raise ValueError("Remotes auction page must be HTML text")
            except Exception as exc:
                failures += 1
                warnings.append(
                    f"Remotes group {group.auction_id}: "
                    f"price lookup failed ({type(exc).__name__})"
                )
                if failures >= self.max_price_failures:
                    warnings.append("Remotes: stopped pricing after repeated refusals")
                    break
                continue
            failures = 0
            prices[group.auction_id] = parse_lot_prices(document)
        return prices, warnings

    def scan(self) -> SourceScanResult:
        try:
            response = self.transport.get(self.discovery_url, timeout=self.timeout)
            document = decode_response(response)
            if not isinstance(document, str):
                raise ValueError("Remotes discovery must be RSS/XML text")
            raw_groups, parse_errors = parse_rss(document)
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Remotes discovery failed ({type(exc).__name__})",),
            )
        groups: list[AuctionGroup] = []
        pending: list[tuple[AuctionGroup, Mapping[str, Any], datetime]] = []
        errors = list(parse_errors)
        for raw in raw_groups:
            group_id = str(raw["id"])
            started = datetime.now(UTC)
            group = AuctionGroup(
                source_id=self.source_id,
                auction_id=group_id,
                title=clean_text(raw.get("title")) or f"Remate {group_id}",
                url=str(raw["url"]),
                category="",
                active=True,
                closing_at=_group_closing(raw),
                observed_at=started,
            )
            groups.append(group)
            pending.append((group, raw, started))

        prices, warnings = self._lot_prices(groups)

        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        for group, raw, started in pending:
            group_id = group.auction_id
            group_prices = prices.get(group_id, {})
            group_lots: list[AuctionLot] = []
            for item in raw["lots"]:
                try:
                    title = clean_text(item.get("title"))
                    lot_id = clean_text(item.get("id"))
                    if not title or not lot_id:
                        raise ValueError("lot lacks stable id or title")
                    priced = group_prices.get(lot_id)
                    group_lots.append(
                        AuctionLot(
                            source_id=self.source_id,
                            auction_id=group_id,
                            lot_id=lot_id,
                            title=title,
                            description=clean_text(item.get("description")),
                            category="",
                            price_value=priced[0] if priced else None,
                            price_currency=priced[1] if priced else None,
                            price_label=_price_label(*priced) if priced else "",
                            closing_at=group.closing_at,
                            lot_url=str(item["url"]),
                            auction_url=group.url,
                            image_url=first_image(item.get("image"), base=BASE_URL),
                            active=True,
                            observed_at=started,
                        )
                    )
                except (KeyError, ValueError):
                    errors.append(f"Remotes group {group_id}: malformed lot")
            lots.extend(group_lots)
            declared = (
                int(raw.get("declared_lots") or 0)
                if str(raw.get("declared_lots") or "").isdigit()
                else None
            )
            partial = declared is not None and declared != len(group_lots)
            if partial:
                errors.append(f"Remotes group {group_id}: declared lot count mismatch")
            receipts.append(
                GroupReceipt(
                    group_id=group_id,
                    status="partial" if partial else "complete",
                    inventory_authoritative=not partial,
                    lot_count=len(group_lots),
                    error_count=1 if partial else 0,
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
            warnings=tuple(warnings),
        )
