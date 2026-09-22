"""Firecrawl web tools for the researcher agent (ADK FunctionTool).

These two plain Python functions are handed to the researcher LlmAgent as tools.
ADK wraps each function into a FunctionTool automatically and shows the model the
function name, the argument names/types and the docstring - so the docstrings
below are really the tool descriptions the model reads. Keep them clear.

The actual HTTP work is done by firecrawl_client.py, which already lives in the
project root (the same file the legacy v1 code uses), so Firecrawl logic is only
written once. It uses the standard library only and needs FIRECRAWL_API_KEY,
which is read from .env when the tool is called.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable so we can reuse firecrawl_client.py.
# We append (not insert) so the standard library and site-packages keep priority.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from firecrawl_client import FirecrawlClient, FirecrawlError, WebPage  # noqa: E402

# Keep each tool result small: the text goes straight into the model's context.
MAX_PAGE_CHARS = 4000

_client: FirecrawlClient | None = None


def _get_client() -> FirecrawlClient:
    """Create the Firecrawl client on first use (so importing this file is free)."""
    global _client
    if _client is None:
        _client = FirecrawlClient()
    return _client


def _page_to_dict(page: WebPage) -> dict:
    """Convert one Firecrawl page into a small JSON-friendly dict for the model."""
    text = (page.markdown or page.description).strip()
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n[truncated]"
    return {"title": page.title or page.url, "url": page.url, "content": text}


def search_web(query: str, max_results: int = 3) -> dict:
    """Search the live web with Firecrawl and return the top pages with their text.

    Args:
        query: What to search for, e.g. "how does a capacitor store charge".
        max_results: How many pages to return (1-5). Use 1-2 for narrow questions.

    Returns:
        A dict with "status" ("success" or "error"), the "query", and a "pages" list
        where each page has "title", "url" and "content" (markdown text).
    """
    try:
        pages = _get_client().search(query, limit=max(1, min(int(max_results), 5)))
    except FirecrawlError as exc:
        # Never raise out of a tool: tell the model what went wrong instead.
        return {"status": "error", "query": query, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
        return {"status": "error", "query": query, "message": f"unexpected error: {exc}"}

    if not pages:
        return {"status": "success", "query": query, "pages": [], "message": "no results found"}
    return {"status": "success", "query": query, "pages": [_page_to_dict(p) for p in pages]}


def scrape_page(url: str) -> dict:
    """Fetch one web page with Firecrawl and return its main text as markdown.

    Args:
        url: The full URL to read, e.g. "https://en.wikipedia.org/wiki/Capacitor".

    Returns:
        A dict with "status" ("success" or "error"), "title", "url" and "content".
    """
    try:
        page = _get_client().scrape(url)
    except FirecrawlError as exc:
        return {"status": "error", "url": url, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
        return {"status": "error", "url": url, "message": f"unexpected error: {exc}"}

    return {"status": "success", **_page_to_dict(page)}
