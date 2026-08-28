"""Remotes RSS adapter with strict XML structure and stable query IDs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
from auction_watch.sources.transport import decode_response

BASE_URL = "https://www.remotes.com.uy"
FEED_URL = f"{BASE_URL}/feed/publicados"


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
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
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
                closing_at=utc_datetime(raw.get("closing_at")),
                observed_at=started,
            )
            groups.append(group)
            group_lots: list[AuctionLot] = []
            for item in raw["lots"]:
                try:
                    title = clean_text(item.get("title"))
                    lot_id = clean_text(item.get("id"))
                    if not title or not lot_id:
                        raise ValueError("lot lacks stable id or title")
                    group_lots.append(
                        AuctionLot(
                            source_id=self.source_id,
                            auction_id=group_id,
                            lot_id=lot_id,
                            title=title,
                            description=clean_text(item.get("description")),
                            category="",
                            price_value=decimal_value(item.get("price")),
                            price_currency="UYU" if item.get("price") is not None else None,
                            price_label=clean_text(item.get("price")),
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
        )
