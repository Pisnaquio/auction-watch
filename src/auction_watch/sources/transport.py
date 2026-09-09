"""Transport abstraction used by source adapters and their deterministic tests."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from time import monotonic
from typing import Any, Protocol

import httpx

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 10.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Return the server's requested wait, ignoring the HTTP-date form."""

    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        return None


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Return a response-like object or decoded mapping."""


class HttpxTransport:
    """Small production transport; adapters remain unaware of httpx details."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or httpx.Client(follow_redirects=True)
        self._owns_client = client is None
        self.clock = clock
        self.sleep = sleep
        self.client.headers["User-Agent"] = "Auction Watch/0.1 (+public source adapter; read-only)"
        self.client.headers[
            "Accept"
        ] = "application/json, text/html, application/rss+xml, application/xml;q=0.9"

    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
    ) -> httpx.Response:
        for attempt in range(2):
            request_timeout = timeout
            if deadline is not None:
                request_timeout = min(timeout, deadline - self.clock())
                if request_timeout <= 0:
                    raise RuntimeError("transport deadline exceeded")
            try:
                response = self.client.get(url, timeout=request_timeout, headers=headers)
                if response.status_code in RETRY_STATUS and attempt == 0:
                    # Retrying a throttled source immediately only digs the hole
                    # deeper, so wait — and for as long as the server asked.
                    wait = _retry_after_seconds(response) or RETRY_BACKOFF_SECONDS
                    response.close()
                    self._pause(wait, deadline)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 0:
                    if deadline is not None and deadline - self.clock() <= 0:
                        raise RuntimeError("transport deadline exceeded") from exc
                    self._pause(RETRY_BACKOFF_SECONDS, deadline)
                    continue
                raise
        raise RuntimeError("transport retry loop exhausted")

    def _pause(self, seconds: float, deadline: float | None) -> None:
        if seconds <= 0:
            return
        if deadline is not None and deadline - self.clock() <= seconds:
            raise RuntimeError("transport deadline exceeded")
        self.sleep(seconds)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def decode_response(response: Any) -> Any:
    if isinstance(response, Mapping) or isinstance(response, list):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except (TypeError, ValueError):
            pass
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8")
    raise ValueError("transport response is not JSON, text, or XML-decodable")


def response_headers(response: Any) -> Mapping[str, str]:
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, Mapping) else {}


__all__ = ["HttpxTransport", "Transport", "decode_response", "response_headers"]
