"""Castells adapter: bounded GXState discovery and verifiable lot pagination."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from threading import Lock
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from auction_watch.core.models import AuctionGroup, AuctionLot
from auction_watch.core.normalization import contains_term
from auction_watch.core.validation import external_id
from auction_watch.sources.base import BaseAuctionSource
from auction_watch.sources.contracts import (
    DecoderDiagnostic,
    GroupReceipt,
    SkippedGroup,
    SourceScanResult,
)
from auction_watch.sources.parsing import (
    clean_text,
    decimal_value,
    first_image,
    normalize_currency,
    utc_datetime,
)
from auction_watch.sources.transport import Transport, decode_response

WEB_BASE = "https://subastascastells.com/"
HOME_URL = urljoin(WEB_BASE, "frontend.home.aspx")
LOTS_URL = urljoin(WEB_BASE, "rest/API/Remate/lotes")
LOT_PAGE_SIZE = 500
MAX_PAGES = 20
MAX_WORKERS = 3
MAX_REQUESTS = 160
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_SCAN_SECONDS = 60.0
MAX_ADAPTIVE_DEPTH = 5
MAX_ADAPTIVE_NODES = 80
MAX_ADAPTIVE_KEYS = 24
MAX_ADAPTIVE_ROWS = 50

LOT_ID_KEYS = ("LoteId", "Loteid", "Id", "id")
LOT_TITLE_KEYS = (
    "LoteDescripcion",
    "Descripcion",
    "LoteTitulo",
    "Titulo",
    "Nombre",
    "description",
    "title",
    "name",
)
SEMANTIC_LOT_LIST_KEYS = frozenset(
    {"data", "rows", "items", "lotes", "lots", "records", "entries"}
)
NEXT_KEYS = ("next", "Next", "nextPage")
SENSITIVE_KEY_FRAGMENTS = (
    "auth",
    "cookie",
    "credential",
    "email",
    "password",
    "recipient",
    "secret",
    "smtp",
    "token",
    "user",
)

ART_TITLE_MARKERS = (
    "pinacoteca",
    "pintura",
    "pinturas",
    "arte",
    "obra de arte",
    "obras de arte",
    "escultura",
    "esculturas",
    "acuarela",
    "acuarelas",
    "grabado",
    "grabados",
    "dibujo",
    "dibujos",
    "litografia",
    "litografias",
)
ART_TITLE_MIXED_MARKERS = (
    "antiguedad",
    "antiguedades",
    "mueble",
    "muebles",
    "joya",
    "joyas",
    "reloj",
    "relojes",
    "juguete",
    "juguetes",
    "coleccionable",
    "coleccionables",
    "libro",
    "libros",
    "disco",
    "discos",
    "consola",
    "consolas",
    "videojuego",
    "videojuegos",
    "electronica",
    "vehiculo",
    "vehiculos",
    "maquinaria",
    "herramienta",
    "herramientas",
    "bazar",
    "hogar",
    "varios",
    "general",
)

IssueCategory = Literal[
    "http_error",
    "timeout",
    "invalid_gxstate",
    "invalid_price_currency",
    "pagination",
    "invalid_lot",
    "request_budget",
    "html_response",
    "error_payload",
    "ambiguous_envelope",
    "unverified_empty",
    "lot_shape_drift",
    "structure_drift",
]

ISSUE_LABELS: dict[IssueCategory, str] = {
    "http_error": "HTTP error",
    "timeout": "timeout",
    "invalid_gxstate": "invalid JSON/GXState",
    "invalid_price_currency": "invalid price/currency",
    "pagination": "incomplete pagination",
    "invalid_lot": "invalid lot",
    "request_budget": "request budget exhausted",
    "html_response": "HTML response instead of JSON",
    "error_payload": "error payload",
    "ambiguous_envelope": "ambiguous JSON envelope",
    "unverified_empty": "unverified empty result",
    "lot_shape_drift": "lot shape drift",
    "structure_drift": "structure drift",
}


class _CastellsIssue(RuntimeError):
    def __init__(
        self,
        category: IssueCategory,
        diagnostic: DecoderDiagnostic | None = None,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class _DecodedPage:
    rows: tuple[Any, ...]
    next_value: Any = None
    diagnostic: DecoderDiagnostic | None = None


@dataclass(frozen=True)
class _ListCandidate:
    path: tuple[str, ...]
    rows: tuple[Any, ...]
    next_value: Any = None
    oversized: bool = False


@dataclass(frozen=True)
class _FetchedLots:
    rows: tuple[Mapping[str, Any], ...]
    invalid_entries: int = 0
    issue: IssueCategory | None = None
    diagnostics: tuple[DecoderDiagnostic, ...] = ()


@dataclass(frozen=True)
class _GroupScan:
    group: AuctionGroup
    lots: tuple[AuctionLot, ...]
    receipt: GroupReceipt
    issues: tuple[IssueCategory, ...] = ()
    warnings: tuple[IssueCategory, ...] = ()
    diagnostics: tuple[DecoderDiagnostic, ...] = ()


def _classify_exception(exc: Exception, *, discovery: bool = False) -> IssueCategory:
    if isinstance(exc, _CastellsIssue):
        return exc.category
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_error"
    if isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        return "http_error"
    message = str(exc).lower()
    if "deadline" in message or "timed out" in message or "timeout" in message:
        return "timeout"
    if "request budget" in message:
        return "request_budget"
    if "pagination" in message or "next cycle" in message or "page limit" in message:
        return "pagination"
    if discovery:
        return "invalid_gxstate"
    return "structure_drift"


def _aggregate_issues(
    groups_by_category: Mapping[IssueCategory, set[str]],
    *,
    discovery_counts: Mapping[IssueCategory, int] | None = None,
) -> tuple[str, ...]:
    summaries: list[str] = []
    for category in ISSUE_LABELS:
        group_count = len(groups_by_category.get(category, set()))
        discovery_count = (discovery_counts or {}).get(category, 0)
        if group_count:
            summaries.append(
                f"Castells {ISSUE_LABELS[category]} ({group_count} "
                f"{'group' if group_count == 1 else 'groups'})"
            )
        if discovery_count:
            summaries.append(
                f"Castells {ISSUE_LABELS[category]} ({discovery_count} discovery "
                f"{'record' if discovery_count == 1 else 'records'})"
            )
    return tuple(summaries)


def _irrelevant_art_title(title: str) -> bool:
    """Classify only clearly art-only auction titles as out of scope."""

    if not title or not any(contains_term(title, marker) for marker in ART_TITLE_MARKERS):
        return False
    return not any(contains_term(title, marker) for marker in ART_TITLE_MIXED_MARKERS)


def parse_gxstate(document: str) -> tuple[Mapping[str, Any], ...]:
    if "GXState" not in document or "RemateImagen" not in document:
        raise _CastellsIssue("invalid_gxstate")
    records: list[Mapping[str, Any]] = []
    for raw in re.findall(r'\{[^{}]*"RemateImagen"[^{}]*"RemateNombre"[^{}]*\}', document):
        try:
            item = json.loads(html.unescape(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping):
            records.append(item)
    if not records:
        raise _CastellsIssue("invalid_gxstate")
    return tuple(records)


def _canonical_auctions(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    Counter[IssueCategory],
    frozenset[str],
]:
    """Collapse repeated GXState records and isolate invalid discovery entries."""

    ordered = sorted(
        records,
        key=lambda raw: (
            clean_text(raw.get("RemateId")),
            json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    unique: dict[str, Mapping[str, Any]] = {}
    discovery_issues: Counter[IssueCategory] = Counter()
    conflicted: set[str] = set()
    for raw in ordered:
        raw_group_id = clean_text(raw.get("RemateId"))
        try:
            group_id = external_id(raw_group_id, "auction_id")
        except ValueError:
            discovery_issues["structure_drift"] += 1
            continue
        previous = unique.get(group_id)
        if previous is None:
            unique[group_id] = raw
            continue
        if previous != raw:
            conflicted.add(group_id)
    return tuple(unique.values()), discovery_issues, frozenset(conflicted)


def _same_source_url(value: Any, fallback: str) -> str:
    candidate = urljoin(WEB_BASE, clean_text(value)) if value else fallback
    parsed = urlsplit(candidate)
    expected = urlsplit(WEB_BASE)
    if parsed.scheme in {"http", "https"} and parsed.netloc == expected.netloc:
        return candidate
    return fallback


def _safe_key(value: object) -> str:
    text = str(value)
    lowered = text.casefold()
    safe = re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,39}", text) is not None
    if safe and not any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"key_{digest}"


def _safe_keys(value: Mapping[Any, Any]) -> tuple[str, ...]:
    return tuple(
        sorted({_safe_key(key) for key in islice(value, MAX_ADAPTIVE_KEYS)})
    )


def _path_text(path: tuple[str, ...]) -> str:
    return "$" + "".join(f".{part}" for part in path)


def _next_value(node: Mapping[Any, Any]) -> Any:
    return next((node[name] for name in NEXT_KEYS if node.get(name)), None)


def _list_candidates(payload: Any) -> tuple[_ListCandidate, ...]:
    candidates: list[_ListCandidate] = []
    visited = 0

    if isinstance(payload, list):
        rows = tuple(islice(payload, LOT_PAGE_SIZE + 1))
        return (_ListCandidate((), rows, oversized=len(rows) > LOT_PAGE_SIZE),)

    def visit(node: Any, path: tuple[str, ...], depth: int) -> None:
        nonlocal visited
        if (
            visited >= MAX_ADAPTIVE_NODES
            or depth > MAX_ADAPTIVE_DEPTH
            or not isinstance(node, Mapping)
        ):
            return
        visited += 1
        entries = sorted(
            islice(node.items(), MAX_ADAPTIVE_KEYS), key=lambda item: str(item[0])
        )
        for raw_key, value in entries:
            key = _safe_key(raw_key)
            child_path = (*path, key)
            if isinstance(value, list):
                rows = tuple(islice(value, LOT_PAGE_SIZE + 1))
                candidates.append(
                    _ListCandidate(
                        child_path,
                        rows,
                        _next_value(node),
                        len(rows) > LOT_PAGE_SIZE,
                    )
                )
            elif isinstance(value, Mapping):
                visit(value, child_path, depth + 1)

    visit(payload, (), 0)
    return tuple(candidates)


def _alias_value(item: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    direct = next((item[key] for key in aliases if item.get(key)), None)
    if direct is not None:
        return direct
    folded = {
        str(key).casefold(): value
        for key, value in islice(item.items(), MAX_ADAPTIVE_KEYS)
    }
    return next((folded[key.casefold()] for key in aliases if folded.get(key.casefold())), "")


def _lot_identity(item: Mapping[str, Any]) -> str:
    return clean_text(_alias_value(item, LOT_ID_KEYS))


def _lot_title(item: Mapping[str, Any]) -> str:
    return clean_text(_alias_value(item, LOT_TITLE_KEYS))


def _candidate_confidence(candidate: _ListCandidate) -> Literal["high", "medium", "low"]:
    if candidate.oversized:
        return "low"
    sample = candidate.rows[:MAX_ADAPTIVE_ROWS]
    if not sample:
        return "low"
    mappings = tuple(item for item in sample if isinstance(item, Mapping))
    if len(mappings) / len(sample) < 0.5:
        return "low"
    identities = tuple(_lot_identity(item) for item in mappings)
    titles = tuple(_lot_title(item) for item in mappings)
    shaped = sum(
        bool(identity and title)
        for identity, title in zip(identities, titles, strict=True)
    )
    unique_identities = [identity for identity in identities if identity]
    if (
        len(mappings) / len(sample) >= 0.8
        and shaped / len(mappings) >= 0.8
        and len(unique_identities) == len(set(unique_identities))
    ):
        return "high"
    if any(identities) or any(titles):
        return "medium"
    return "low"


def _fingerprint(payload: Any, candidates: tuple[_ListCandidate, ...]) -> str:
    if isinstance(payload, Mapping):
        root = f"root=object[{','.join(_safe_keys(payload))}]"
    elif isinstance(payload, list):
        root = "root=list"
    elif isinstance(payload, str):
        root = "root=string"
    else:
        root = f"root={type(payload).__name__}"
    details: list[str] = []
    for candidate in candidates[:6]:
        item_keys: set[str] = set()
        for item in candidate.rows[:5]:
            if isinstance(item, Mapping):
                item_keys.update(_safe_keys(item))
        details.append(
            f"{_path_text(candidate.path)}=list[{len(candidate.rows)}]"
            f"{{{','.join(sorted(item_keys)[:MAX_ADAPTIVE_KEYS])}}}"
        )
    value = ";".join((root, *details))
    return value[:500]


def _error_payload(payload: Any, depth: int = 0) -> bool:
    if depth > 3 or not isinstance(payload, Mapping):
        return False
    for raw_key, value in islice(payload.items(), MAX_ADAPTIVE_KEYS):
        key = str(raw_key).casefold()
        if (
            key in {"error", "errors", "exception"}
            and value is not None
            and value != ""
            and value is not False
        ):
            return True
        if key in {"status", "result"} and isinstance(value, str):
            if value.casefold() in {"error", "failed", "failure"}:
                return True
        if (
            key in {"status", "statuscode"}
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 400
        ):
            return True
        if isinstance(value, Mapping) and _error_payload(value, depth + 1):
            return True
    return False


def _diagnostic(
    group_id: str,
    *,
    status: Literal["adaptive_recovered", "shadow_only"],
    category: Literal[
        "envelope_drift",
        "html_response",
        "error_payload",
        "ambiguous_envelope",
        "unverified_empty",
        "lot_shape_drift",
        "structure_drift",
    ],
    confidence: Literal["high", "medium", "low"],
    payload: Any,
    candidates: tuple[_ListCandidate, ...] = (),
    path: tuple[str, ...] | None = None,
) -> DecoderDiagnostic:
    return DecoderDiagnostic(
        group_id=group_id,
        status=status,
        category=category,
        confidence=confidence,
        path=_path_text(path) if path is not None else None,
        fingerprint=_fingerprint(payload, candidates),
    )


def _decode_lot_page(payload: Any, group_id: str) -> _DecodedPage:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            category: IssueCategory = (
                "html_response" if payload.lstrip().startswith("<") else "structure_drift"
            )
            diagnostic = _diagnostic(
                group_id,
                status="shadow_only",
                category="html_response" if category == "html_response" else "structure_drift",
                confidence="low",
                payload=payload,
            )
            raise _CastellsIssue(category, diagnostic) from exc
    if _error_payload(payload):
        candidates = _list_candidates(payload)
        diagnostic = _diagnostic(
            group_id,
            status="shadow_only",
            category="error_payload",
            confidence="high",
            payload=payload,
            candidates=candidates,
        )
        raise _CastellsIssue("error_payload", diagnostic)
    node = payload
    for _depth in range(3):
        if not isinstance(node, Mapping):
            break
        for key in ("data", "Data", "rows", "Rows"):
            rows = node.get(key)
            if isinstance(rows, list):
                return _DecodedPage(tuple(rows), _next_value(node))
        nested = next(
            (
                node[name]
                for name in ("result", "Result", "payload")
                if isinstance(node.get(name), Mapping)
            ),
            None,
        )
        if nested is None:
            break
        node = nested

    candidates = _list_candidates(payload)
    high = tuple(
        candidate for candidate in candidates if _candidate_confidence(candidate) == "high"
    )
    if len(high) == 1:
        candidate = high[0]
        return _DecodedPage(
            candidate.rows,
            candidate.next_value,
            _diagnostic(
                group_id,
                status="adaptive_recovered",
                category="envelope_drift",
                confidence="high",
                payload=payload,
                candidates=candidates,
                path=candidate.path,
            ),
        )
    if len(high) > 1:
        diagnostic = _diagnostic(
            group_id,
            status="shadow_only",
            category="ambiguous_envelope",
            confidence="medium",
            payload=payload,
            candidates=candidates,
        )
        raise _CastellsIssue("ambiguous_envelope", diagnostic)

    empty = tuple(candidate for candidate in candidates if not candidate.rows)
    if len(candidates) == 1 and len(empty) == 1:
        candidate = empty[0]
        terminal = candidate.path[-1].casefold() if candidate.path else ""
        if terminal in SEMANTIC_LOT_LIST_KEYS:
            return _DecodedPage(
                (),
                candidate.next_value,
                _diagnostic(
                    group_id,
                    status="adaptive_recovered",
                    category="envelope_drift",
                    confidence="high",
                    payload=payload,
                    candidates=candidates,
                    path=candidate.path,
                ),
            )
        diagnostic = _diagnostic(
            group_id,
            status="shadow_only",
            category="unverified_empty",
            confidence="low",
            payload=payload,
            candidates=candidates,
            path=candidate.path,
        )
        raise _CastellsIssue("unverified_empty", diagnostic)

    medium = tuple(
        candidate for candidate in candidates if _candidate_confidence(candidate) == "medium"
    )
    if len(medium) == 1 and len(candidates) == 1:
        candidate = medium[0]
        diagnostic = _diagnostic(
            group_id,
            status="shadow_only",
            category="lot_shape_drift",
            confidence="medium",
            payload=payload,
            candidates=candidates,
            path=candidate.path,
        )
        raise _CastellsIssue("lot_shape_drift", diagnostic)
    if len(candidates) > 1:
        diagnostic = _diagnostic(
            group_id,
            status="shadow_only",
            category="ambiguous_envelope",
            confidence="low",
            payload=payload,
            candidates=candidates,
        )
        raise _CastellsIssue("ambiguous_envelope", diagnostic)
    diagnostic = _diagnostic(
        group_id,
        status="shadow_only",
        category="lot_shape_drift" if candidates else "structure_drift",
        confidence="low",
        payload=payload,
        candidates=candidates,
        path=candidates[0].path if len(candidates) == 1 else None,
    )
    raise _CastellsIssue(
        "lot_shape_drift" if candidates else "structure_drift",
        diagnostic,
    )


def _pagination_url(current_url: str, next_value: Any) -> str:
    candidate = urljoin(current_url, clean_text(next_value))
    parsed = urlsplit(candidate)
    expected = urlsplit(LOTS_URL)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != expected.netloc:
        raise _CastellsIssue("pagination")
    if parsed.path.rstrip("/") != expected.path.rstrip("/"):
        raise _CastellsIssue("pagination")
    return candidate


def _lot_cursor(item: Any) -> str:
    if not isinstance(item, Mapping):
        return ""
    raw = _lot_identity(item)
    return raw.rsplit(":", 1)[-1] if raw else ""


class CastellsSource(BaseAuctionSource):
    source_id = "castells"
    label = "Castells"
    discovery_url = HOME_URL

    def __init__(
        self,
        transport: Transport,
        *,
        timeout: float | None = REQUEST_TIMEOUT_SECONDS,
        max_workers: int = MAX_WORKERS,
        max_requests: int = MAX_REQUESTS,
        deadline_seconds: float = MAX_SCAN_SECONDS,
        page_size: int = LOT_PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(transport, timeout=timeout)
        if max_workers < 1:
            raise ValueError("Castells max_workers must be positive")
        if max_requests < 1:
            raise ValueError("Castells max_requests must be positive")
        if deadline_seconds <= 0:
            raise ValueError("Castells deadline_seconds must be positive")
        if page_size < 1 or page_size > LOT_PAGE_SIZE:
            raise ValueError("Castells page_size is outside the safe bound")
        if max_pages < 1 or max_pages > MAX_PAGES:
            raise ValueError("Castells max_pages is outside the safe bound")
        self.max_workers = min(max_workers, MAX_WORKERS)
        self.max_requests = max_requests
        self.deadline_seconds = deadline_seconds
        self.page_size = page_size
        self.max_pages = max_pages
        self.clock = clock
        self._request_count = 0
        self._request_lock = Lock()
        self._deadline: float | None = None

    def _get(self, url: str) -> Any:
        with self._request_lock:
            if self._deadline is None:
                raise RuntimeError("Castells scan deadline is not initialized")
            remaining = self._deadline - self.clock()
            if remaining <= 0:
                raise _CastellsIssue("timeout")
            if self._request_count >= self.max_requests:
                raise _CastellsIssue("request_budget")
            self._request_count += 1
        try:
            return decode_response(
                self.transport.get(
                    url,
                    timeout=min(self.timeout, remaining),
                    deadline=self._deadline,
                )
            )
        except Exception as exc:
            raise _CastellsIssue(_classify_exception(exc)) from exc

    def _fetch_lots(self, group: AuctionGroup, remate_type: int) -> _FetchedLots:
        params: dict[str, Any] = {
            "Remateid": group.auction_id,
            "RemateTipo": remate_type,
            "Cerrado": "false",
            "Lastloteid": 0,
            "Limit": self.page_size,
            "Timezoneoffset": -180,
            "ClienteId": 0,
        }
        rows: list[Mapping[str, Any]] = []
        invalid_entries = 0
        diagnostics: list[DecoderDiagnostic] = []
        request_url = f"{LOTS_URL}?{urlencode(params)}"
        seen_urls: set[str] = set()
        seen_cursors: set[str] = set()
        for _page in range(self.max_pages):
            if request_url in seen_urls:
                return _FetchedLots(
                    tuple(rows), invalid_entries, "pagination", tuple(diagnostics)
                )
            seen_urls.add(request_url)
            try:
                payload = self._get(request_url)
                decoded = _decode_lot_page(payload, group.auction_id)
                page_rows = decoded.rows
                next_value = decoded.next_value
                if decoded.diagnostic is not None:
                    diagnostics.append(decoded.diagnostic)
            except Exception as exc:
                category = _classify_exception(exc)
                diagnostic = exc.diagnostic if isinstance(exc, _CastellsIssue) else None
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
                if rows:
                    return _FetchedLots(
                        tuple(rows), invalid_entries, category, tuple(diagnostics)
                    )
                raise _CastellsIssue(category, diagnostic) from exc
            mappings = tuple(item for item in page_rows if isinstance(item, Mapping))
            invalid_entries += len(page_rows) - len(mappings)
            rows.extend(mappings)
            if next_value:
                try:
                    request_url = _pagination_url(request_url, next_value)
                except _CastellsIssue:
                    return _FetchedLots(
                        tuple(rows), invalid_entries, "pagination", tuple(diagnostics)
                    )
                continue
            if len(page_rows) < self.page_size:
                return _FetchedLots(
                    tuple(rows), invalid_entries, diagnostics=tuple(diagnostics)
                )
            cursor = _lot_cursor(page_rows[-1] if page_rows else None)
            if not cursor or cursor in seen_cursors:
                return _FetchedLots(
                    tuple(rows), invalid_entries, "pagination", tuple(diagnostics)
                )
            seen_cursors.add(cursor)
            params["Lastloteid"] = cursor
            request_url = f"{LOTS_URL}?{urlencode(params)}"
        return _FetchedLots(
            tuple(rows), invalid_entries, "pagination", tuple(diagnostics)
        )

    def _scan_group(self, raw: Mapping[str, Any]) -> _GroupScan:
        group_id = external_id(clean_text(raw.get("RemateId")), "auction_id")
        started = datetime.now(UTC)
        canonical_group_url = urljoin(
            WEB_BASE,
            f"frontend.sitio.visualremate.aspx?Remate={group_id}",
        )
        group = AuctionGroup(
            source_id=self.source_id,
            auction_id=group_id,
            title=clean_text(raw.get("RemateNombre")) or f"Remate {group_id}",
            url=_same_source_url(raw.get("Link"), canonical_group_url),
            category=clean_text(raw.get("RemateCategoriaNombre")),
            active=True,
            closing_at=utc_datetime(raw.get("RemateCierre")),
            observed_at=started,
        )
        issues: list[IssueCategory] = []
        warnings: list[IssueCategory] = []
        try:
            try:
                remate_type = int(raw.get("RemateTipo") or 1)
                if remate_type < 1:
                    raise ValueError
            except (TypeError, ValueError):
                remate_type = 1
                issues.append("structure_drift")
            fetched = self._fetch_lots(group, remate_type)
            issues.extend("invalid_lot" for _ in range(fetched.invalid_entries))
            if fetched.issue is not None:
                issues.append(fetched.issue)
            group_lots: dict[str, AuctionLot] = {}
            conflicted_lot_ids: set[str] = set()
            for item in fetched.rows:
                try:
                    lot_id = _lot_identity(item)
                    title = _lot_title(item)
                    if not lot_id or not title:
                        raise ValueError("lot lacks stable identity")
                    canonical_lot_url = urljoin(
                        WEB_BASE,
                        "frontend.sitio.visualremate.aspx?"
                        + urlencode({"Remate": group_id, "Lote": lot_id}),
                    )
                    lot_url = _same_source_url(item.get("DetalleUrl"), canonical_lot_url)
                    price_label = clean_text(
                        item.get("ValorActual") or item.get("LotePrecioSalida")
                    )
                    price = decimal_value(price_label)
                    currency = normalize_currency(item.get("LotePrecioSalidaMonedaWF"))
                    if price_label and (price is None or currency is None):
                        warnings.append("invalid_price_currency")
                        price = None
                        currency = None
                    raw_image = first_image(
                        item.get("Imagen") or item.get("image"), base=WEB_BASE
                    )
                    image_url = (_same_source_url(raw_image, "") if raw_image else None) or None
                    candidate = AuctionLot(
                        source_id=self.source_id,
                        auction_id=group_id,
                        lot_id=lot_id,
                        title=title,
                        description=clean_text(
                            item.get("LoteDetalle") or item.get("Detalle")
                        )
                        or title,
                        category=group.category,
                        price_value=price,
                        price_currency=currency if price is not None else None,
                        price_label=price_label,
                        closing_at=utc_datetime(item.get("LoteCierre")) or group.closing_at,
                        lot_url=lot_url,
                        auction_url=group.url,
                        image_url=image_url,
                        active=clean_text(item.get("Estado") or "active").lower()
                        not in {"cerrado", "closed"},
                        observed_at=started,
                    )
                    if lot_id in conflicted_lot_ids:
                        continue
                    previous = group_lots.get(lot_id)
                    if previous is None:
                        group_lots[lot_id] = candidate
                    elif previous != candidate:
                        group_lots.pop(lot_id)
                        conflicted_lot_ids.add(lot_id)
                        issues.append("invalid_lot")
                except (TypeError, ValueError):
                    issues.append("invalid_lot")
            ordered_lots = tuple(group_lots[key] for key in sorted(group_lots))
            status = "partial" if issues else "complete"
            receipt = GroupReceipt(
                group_id=group_id,
                status=status,
                inventory_authoritative=status == "complete",
                lot_count=len(ordered_lots),
                error_count=len(issues),
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return _GroupScan(
                group,
                ordered_lots,
                receipt,
                tuple(issues),
                tuple(warnings),
                fetched.diagnostics,
            )
        except Exception as exc:
            category = _classify_exception(exc)
            diagnostics = (
                (exc.diagnostic,)
                if isinstance(exc, _CastellsIssue) and exc.diagnostic is not None
                else ()
            )
            receipt = GroupReceipt(
                group_id=group_id,
                status="failed",
                inventory_authoritative=False,
                lot_count=0,
                error_count=1,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return _GroupScan(group, (), receipt, (category,), diagnostics=diagnostics)

    def scan(self) -> SourceScanResult:
        with self._request_lock:
            self._request_count = 0
            self._deadline = self.clock() + self.deadline_seconds
        try:
            document = self._get(self.discovery_url)
            if not isinstance(document, str):
                raise _CastellsIssue("invalid_gxstate")
            auctions, discovery_issues, conflicted_groups = _canonical_auctions(
                parse_gxstate(document)
            )
        except Exception as exc:
            category = _classify_exception(exc, discovery=True)
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                discovery_status="failed",
                errors=(f"Castells {ISSUE_LABELS[category]} (discovery)",),
            )

        relevant_auctions: list[Mapping[str, Any]] = []
        skipped_groups: list[SkippedGroup] = []
        for raw in auctions:
            group_id = external_id(clean_text(raw.get("RemateId")), "auction_id")
            title = clean_text(raw.get("RemateNombre"))
            if group_id not in conflicted_groups and _irrelevant_art_title(title):
                skipped_groups.append(SkippedGroup(group_id=group_id, title=title))
            else:
                relevant_auctions.append(raw)

        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        receipts: list[GroupReceipt] = []
        diagnostics: list[DecoderDiagnostic] = []
        issue_groups: dict[IssueCategory, set[str]] = {
            category: set() for category in ISSUE_LABELS
        }
        warning_groups: dict[IssueCategory, set[str]] = {
            category: set() for category in ISSUE_LABELS
        }
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for scanned in executor.map(self._scan_group, relevant_auctions):
                receipt = scanned.receipt
                issues = list(scanned.issues)
                if receipt.group_id in conflicted_groups:
                    issues.append("structure_drift")
                    receipt = receipt.model_copy(
                        update={
                            "status": "failed" if receipt.status == "failed" else "partial",
                            "inventory_authoritative": False,
                            "error_count": receipt.error_count + 1,
                        }
                    )
                groups.append(scanned.group)
                lots.extend(scanned.lots)
                receipts.append(receipt)
                diagnostics.extend(scanned.diagnostics)
                for category in set(issues):
                    issue_groups[category].add(receipt.group_id)
                for category in set(scanned.warnings):
                    warning_groups[category].add(receipt.group_id)
        errors = _aggregate_issues(issue_groups, discovery_counts=discovery_issues)
        warnings = _aggregate_issues(warning_groups)
        unique_diagnostics = {
            (
                item.group_id,
                item.status,
                item.category,
                item.path,
                item.fingerprint,
            ): item
            for item in diagnostics
        }
        authoritative = not errors and all(
            receipt.status == "complete" and receipt.inventory_authoritative
            for receipt in receipts
        )
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=tuple(groups),
            lots=tuple(lots),
            discovery_status="complete" if authoritative else "partial",
            inventory_authoritative=authoritative,
            # GXState is not an authoritative auction-lifecycle feed. A group
            # can disappear from the home page temporarily, so omission alone
            # must never deactivate its previously healthy inventory.
            omission_authoritative=False,
            receipts=tuple(receipts),
            skipped_groups=tuple(skipped_groups),
            diagnostics=tuple(unique_diagnostics.values()),
            errors=errors,
            warnings=warnings,
        )
