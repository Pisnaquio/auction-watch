"""Transport abstraction used by source adapters and their deterministic tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import httpx


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Return a response-like object or decoded mapping."""


class HttpxTransport:
    """Small production transport; adapters remain unaware of httpx details."""

    def __init__(
        self,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(follow_redirects=True)
        self._owns_client = client is None
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
    ) -> httpx.Response:
        for attempt in range(2):
            try:
                response = self.client.get(url, timeout=timeout, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    response.close()
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 0:
                    continue
                raise
        raise RuntimeError("transport retry loop exhausted")

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
