"""Minimal Firecrawl client for the researcher agent (standard library only).

Talks to the Firecrawl v2 REST API directly with urllib, so no new dependency is
needed and nothing extra has to be bundled. The HTTP transport is injectable,
which keeps offline tests possible.

Auth: put FIRECRAWL_API_KEY=fc-... in .env (or in the environment).
Docs: https://docs.firecrawl.dev/api-reference/endpoint/scrape
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

FIRECRAWL_API_BASE = "https://api.firecrawl.dev/v2"
FIRECRAWL_KEY_ENV = "FIRECRAWL_API_KEY"
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# transport(url, body, headers, timeout) -> (status_code, response_body)
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


class FirecrawlError(Exception):
    """Raised when Firecrawl is unreachable, misconfigured, or returns an error."""


@dataclass
class WebPage:
    """One page returned by Firecrawl (scrape) or found by Firecrawl (search)."""

    url: str
    title: str = ""
    markdown: str = ""
    description: str = ""


def _urlopen_transport(
    url: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    """Default transport: POST `body` to `url` with urllib and return status + raw bytes."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON body worth reading
        return int(exc.code), exc.read()


def _decode(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_text(parsed: dict[str, Any] | None, status: int) -> str:
    if parsed is not None:
        for key in ("error", "message", "details"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return f"Firecrawl error {status}: {value.strip()}"
    return f"Firecrawl error {status}"


def _as_page(item: Any, fallback_url: str = "") -> WebPage | None:
    """Build a WebPage from a scrape/search result dict, tolerating shape differences."""
    if not isinstance(item, dict):
        return None

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    url = item.get("url") or metadata.get("sourceURL") or metadata.get("url") or fallback_url
    if not isinstance(url, str) or not url:
        return None

    markdown = item.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        highlights = item.get("highlights")
        if isinstance(highlights, list):
            markdown = "\n".join(str(h) for h in highlights if isinstance(h, str))

    return WebPage(
        url=url,
        title=str(item.get("title") or metadata.get("title") or ""),
        markdown=markdown if isinstance(markdown, str) else "",
        description=str(item.get("description") or metadata.get("description") or ""),
    )


def _iter_results(data: Any) -> list[Any]:
    """Accept the v2 search shape ({"web": [...]}) and the v1 shape ([...])."""
    if isinstance(data, dict):
        for key in ("web", "news"):
            items = data.get(key)
            if isinstance(items, list):
                return items
        return []
    if isinstance(data, list):
        return data
    return []


class FirecrawlClient:
    """Thin Firecrawl client with retries, JSON parsing and lazy key lookup."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = FIRECRAWL_API_BASE,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._backoff_seconds = backoff_seconds
        self._transport = transport or _urlopen_transport

    def api_key(self) -> str:
        """Resolve the key per call, so .env edits are picked up without a restart."""
        raw = self._api_key if self._api_key is not None else os.environ.get(
            FIRECRAWL_KEY_ENV, ""
        )
        key = (raw or "").strip()
        if not key:
            raise FirecrawlError(f"Set the {FIRECRAWL_KEY_ENV} environment variable first")
        return key

    def scrape(self, url: str) -> WebPage:
        """Fetch one URL and return its main content as markdown."""
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        data = self._post("/scrape", payload).get("data")
        page = _as_page(data, fallback_url=url)
        if page is None:
            raise FirecrawlError(f"Firecrawl returned no content for {url}")
        return page

    def search(
        self, query: str, limit: int = 3, with_content: bool = True
    ) -> list[WebPage]:
        """Search the web and return the top results, optionally with markdown content."""
        payload: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(int(limit), 100)),
            "sources": ["web"],
        }
        if with_content:
            payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
            }

        data = self._post("/search", payload).get("data")
        pages = [_as_page(item) for item in _iter_results(data)]
        return [page for page in pages if page is not None]

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to Firecrawl, retrying transient failures, and return the parsed body."""
        key = self.api_key()
        url = f"{self._base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        last_error: Any = "no attempt made"

        for attempt in range(self._max_retries + 1):
            try:
                status, raw = self._transport(url, body, headers, self._timeout)
            except OSError as exc:  # DNS failure, connection refused, timeout, TLS
                last_error = exc
            else:
                parsed = _decode(raw)
                if 200 <= status < 300:
                    if parsed is None:
                        raise FirecrawlError("Firecrawl returned an unreadable response")
                    if parsed.get("success") is False:
                        raise FirecrawlError(_error_text(parsed, status))
                    return parsed
                if status not in RETRYABLE_STATUSES:
                    raise FirecrawlError(_error_text(parsed, status))
                last_error = FirecrawlError(_error_text(parsed, status))

            if attempt < self._max_retries:
                time.sleep(self._backoff_seconds * (attempt + 1))

        raise FirecrawlError(
            f"Firecrawl request failed after {self._max_retries + 1} attempts: {last_error}"
        )


_default_client: FirecrawlClient | None = None


def get_client() -> FirecrawlClient:
    """Shared client, created on first use. Tests should build their own instead."""
    global _default_client
    if _default_client is None:
        _default_client = FirecrawlClient()
    return _default_client
