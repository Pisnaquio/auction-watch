"""Shared parsing and authority rules for generic auction sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.sources.contracts import GroupReceipt, SourceScanResult
from auction_watch.sources.transport import Transport, decode_response


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(item: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
    return None


def _datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _price(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        raw = str(value).strip()
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _items(payload: Any, *keys: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(item for item in payload if isinstance(item, Mapping))
    if not isinstance(payload, Mapping):
        return ()
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(item for item in value if isinstance(item, Mapping))
    for key in ("data", "result", "payload"):
        if isinstance(payload.get(key), Mapping):
            nested = _items(payload[key], *keys)
            if nested:
                return nested
    return ()


def _has_collection(payload: Any, *keys: str) -> bool:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return True
    if not isinstance(payload, Mapping):
        return False
    return any(
        isinstance(payload.get(key), Sequence)
        and not isinstance(payload.get(key), (str, bytes, bytearray))
        for key in keys
    )


def _active(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _text(value).lower() not in {"closed", "sold", "inactive", "false", "0"}


class BaseAuctionSource(ABC):
    source_id: str
    label: str
    discovery_url: str
    timeout: float = 20.0

    def __init__(self, transport: Transport, *, timeout: float | None = None) -> None:
        self.transport = transport
        if timeout is not None:
            self.timeout = timeout

    @abstractmethod
    def scan(self) -> SourceScanResult:
        """Discover and normalize one source without matching or persistence."""

    def _get(self, url: str) -> Any:
        return decode_response(self.transport.get(url, timeout=self.timeout))

    def _scan_url(
        self,
        *,
        group_keys: tuple[str, ...],
        lot_keys: tuple[str, ...],
        lot_url_key: str | None = None,
    ) -> SourceScanResult:
        try:
            payload = self._get(self.discovery_url)
        except Exception as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"discovery failed ({type(exc).__name__})",),
            )
        return self._scan_payload(
            payload,
            group_keys=group_keys,
            lot_keys=lot_keys,
            lot_url_key=lot_url_key,
        )

    def _group(self, raw: Mapping[str, Any], *, group_id: str, now: datetime) -> AuctionGroup:
        url = _text(_first(raw, "url", "auction_url", "link"))
        if not url:
            url = urljoin(self.discovery_url, f"{group_id}")
        return AuctionGroup(
            source_id=self.source_id,
            auction_id=group_id,
            title=_text(_first(raw, "title", "name", "label")) or f"Grupo {group_id}",
            url=url,
            category=_text(_first(raw, "category", "type")),
            active=_active(_first(raw, "active", "is_active", "published")),
            closing_at=_datetime(_first(raw, "closing_at", "end_date", "ends_at")),
            observed_at=now,
        )

    def _lot(
        self,
        raw: Mapping[str, Any],
        *,
        group: AuctionGroup,
        now: datetime,
    ) -> AuctionLot:
        lot_id = _text(_first(raw, "lot_id", "id", "lotId", "item_id"))
        title = _text(_first(raw, "title", "name", "description"))
        if not lot_id or not title:
            raise ValueError("lot requires stable id and title")
        price = _price(_first(raw, "price_value", "price", "current_price", "amount"))
        currency = _text(_first(raw, "price_currency", "currency", "currency_code")).upper() or None
        lot_url = _text(_first(raw, "lot_url", "url", "link"))
        if not lot_url:
            raise ValueError("lot requires a URL")
        return AuctionLot(
            source_id=self.source_id,
            auction_id=group.auction_id,
            lot_id=lot_id,
            title=title,
            description=_text(_first(raw, "description", "details", "summary")),
            category=_text(_first(raw, "category", "type")) or group.category,
            price_value=price,
            price_currency=currency if price is not None else None,
            price_label=_text(_first(raw, "price_label", "formatted_price")),
            closing_at=_datetime(_first(raw, "closing_at", "end_date", "ends_at"))
            or group.closing_at,
            lot_url=urljoin(group.url, lot_url),
            auction_url=group.url,
            image_url=(
                urljoin(group.url, _text(_first(raw, "image_url", "image", "thumbnail")))
                if _text(_first(raw, "image_url", "image", "thumbnail"))
                else None
            ),
            active=_active(_first(raw, "active", "is_active", "status")),
            observed_at=now,
        )

    def _scan_payload(
        self,
        payload: Any,
        *,
        group_keys: tuple[str, ...],
        lot_keys: tuple[str, ...],
        lot_url_key: str | None = None,
    ) -> SourceScanResult:
        now = datetime.now(UTC)
        raw_groups = _items(payload, *group_keys)
        explicit_complete = isinstance(payload, Mapping) and (
            payload.get("complete") is True
            or payload.get("discovery_complete") is True
            or payload.get("next") in (None, "")
            and any(key in payload for key in group_keys)
        )
        if not raw_groups and not explicit_complete:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=("response did not prove a valid group collection",),
            )
        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        errors: list[str] = []
        seen_groups: set[str] = set()
        for raw_group in raw_groups:
            group_id = _text(_first(raw_group, "auction_id", "group_id", "id", "remate_id"))
            if not group_id or group_id in seen_groups:
                if group_id:
                    errors.append(f"duplicate or empty group id: {group_id}")
                continue
            seen_groups.add(group_id)
            started = datetime.now(UTC)
            try:
                group = self._group(raw_group, group_id=group_id, now=now)
            except Exception as exc:
                errors.append(f"group {group_id}: {type(exc).__name__}")
                receipts.append(
                    GroupReceipt(
                        group_id=group_id,
                        status="failed",
                        error_count=1,
                        lot_count=0,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
                continue
            groups.append(group)
            embedded = _items(raw_group, *lot_keys)
            try:
                fetched = (
                    self._get(_text(raw_group.get(lot_url_key)))
                    if lot_url_key and raw_group.get(lot_url_key)
                    else None
                )
            except Exception as exc:
                fetched = None
                errors.append(f"group {group_id}: fetch failed ({type(exc).__name__})")
            raw_lots = embedded or _items(fetched, *lot_keys)
            has_embedded_lots = _has_collection(raw_group, *lot_keys)
            has_fetched_lots = _has_collection(fetched, *lot_keys)
            group_errors = 0
            for raw_lot in raw_lots:
                try:
                    lots.append(self._lot(raw_lot, group=group, now=now))
                except Exception as exc:
                    group_errors += 1
                    errors.append(f"group {group_id}: malformed lot ({type(exc).__name__})")
            status = (
                "complete"
                if group_errors == 0 and (has_fetched_lots or has_embedded_lots)
                else "partial"
            )
            receipts.append(
                GroupReceipt(
                    group_id=group_id,
                    status=status,
                    inventory_authoritative=status == "complete",
                    lot_count=sum(lot.auction_id == group_id for lot in lots),
                    error_count=group_errors,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
        discovery_status = "complete" if not errors else "partial"
        authoritative = (
            explicit_complete
            and discovery_status == "complete"
            and all(receipt.inventory_authoritative for receipt in receipts)
        )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(groups),
            lots=tuple(lots),
            discovery_status=discovery_status,
            inventory_authoritative=authoritative,
            receipts=tuple(receipts),
            errors=tuple(errors),
        )
