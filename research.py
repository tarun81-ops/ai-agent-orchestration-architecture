"""Firecrawl-backed web research for the researcher agent.

Rather than teaching every agent about tools, this layer wraps the *injected* llm
callable (the same seam documented in ARCHITECTURE.md): when the orchestrator
hands a step to the researcher, the wrapper fetches the URLs named in the step
prompt -- or searches the web with the step instruction -- and appends the
scraped markdown to the researcher's prompt. Every other role passes straight
through, so the pipeline, executor, reviewer and planner keep full control of
the flow and stay unchanged.

It degrades safely: if FIRECRAWL_API_KEY is missing or a request fails, the run
continues with the plain prompt and a one-line note goes to `notify`.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Sequence

from config import FIRECRAWL_MAX_CHARS, FIRECRAWL_MAX_URLS, FIRECRAWL_SEARCH_LIMIT
from firecrawl_client import FirecrawlClient, FirecrawlError, WebPage

RESEARCH_HEADER = "--- Web research (Firecrawl) ---"
DEFAULT_RESEARCH_ROLES = ("researcher",)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_URL_TRAILING_CHARS = ".,;:!?'\")]>"


def extract_urls(text: str, limit: int | None = None) -> list[str]:
    """Return the http(s) URLs in `text`, de-duplicated and in order."""
    found: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING_CHARS)
        if url and url not in found:
            found.append(url)
            if limit is not None and len(found) >= limit:
                break
    return found


def _section(text: str, header: str) -> str:
    """Return the first paragraph that follows `header` in `text`."""
    index = text.find(header)
    if index == -1:
        return ""
    body = text[index + len(header) :].lstrip("\n")
    return " ".join(body.split("\n\n", 1)[0].split()).strip()


def build_query(user: str, limit: int = 200) -> str:
    """Use the step instruction as the search query, falling back to the task."""
    query = _section(user, "Instruction:")
    if not query:
        query = _section(user, "Original Task:")
    if not query:
        query = " ".join(user.split())
    return query.strip().rstrip(".").strip()[:limit]


def format_research_section(pages: Sequence[WebPage], max_chars: int = FIRECRAWL_MAX_CHARS) -> str:
    """Render fetched pages as one prompt section, respecting an overall char budget."""
    blocks = [
        RESEARCH_HEADER,
        "Live pages fetched with Firecrawl for this step. Ground your answer in this "
        "content and cite the source URLs you use.",
    ]
    budget = max(0, max_chars)
    used = 0

    for page in pages:
        if used >= budget:
            break
        body = (page.markdown or page.description).strip()
        if not body:
            continue
        allowed = budget - used
        if len(body) > allowed:
            body = body[:allowed].rstrip() + "\n[truncated]"
        blocks.append(f"### {page.title.strip() or page.url}\nSource: {page.url}\n{body}")
        used += len(body)

    if used == 0:
        return ""
    return "\n\n".join(blocks)

def _fetch_pages(
    client: FirecrawlClient,
    user: str,
    max_urls: int,
    search_limit: int,
    cache: dict[Any, list[WebPage]],
) -> list[WebPage]:
    """Scrape the URLs in the prompt, or search for the step instruction.

    Results are cached per run so a revise round reuses what was already fetched
    instead of spending more Firecrawl credits.
    """
    urls = extract_urls(user, limit=max_urls)
    if urls:
        pages: list[WebPage] = []
        for url in urls:
            key: Any = ("scrape", url)
            if key not in cache:
                cache[key] = [client.scrape(url)]
            pages.extend(cache[key])
        return pages

    query = build_query(user)
    if not query:
        return []
    key = ("search", query, search_limit)
    if key not in cache:
        cache[key] = client.search(query, limit=search_limit)
    return list(cache[key])


def build_research_llm(
    llm: Callable[..., Any],
    client: FirecrawlClient | None = None,
    roles: Sequence[str] = DEFAULT_RESEARCH_ROLES,
    max_urls: int = FIRECRAWL_MAX_URLS,
    search_limit: int = FIRECRAWL_SEARCH_LIMIT,
    max_chars: int = FIRECRAWL_MAX_CHARS,
    notify: Callable[[str], None] | None = None,
    cache: dict[Any, list[WebPage]] | None = None,
) -> Callable[..., Any]:
    """Wrap an llm callable so research steps get live Firecrawl content."""

    active_client = client
    pages_cache: dict[Any, list[WebPage]] = {} if cache is None else cache

    def report(message: str) -> None:
        if notify is not None:
            notify(message)

    def research_llm(role: str, system: str, user: str, json_mode: bool = False) -> Any:
        nonlocal active_client

        # Only the research roles are enriched, and never twice for one prompt.
        if role not in roles or RESEARCH_HEADER in user:
            return llm(role, system, user, json_mode=json_mode)

        try:
            if active_client is None:
                active_client = FirecrawlClient()
            pages = _fetch_pages(active_client, user, max_urls, search_limit, pages_cache)
        except FirecrawlError as exc:
            report(f"WEB    -> web research skipped for {role}: {exc}")
            return llm(role, system, user, json_mode=json_mode)

        section = format_research_section(pages, max_chars=max_chars)
        if not section:
            report("WEB    -> Firecrawl returned nothing usable; continuing without web research")
            return llm(role, system, user, json_mode=json_mode)

        urls = extract_urls(user, limit=max_urls)
        if urls:
            report(f"WEB    -> scraped {len(pages)} URL(s) with Firecrawl for {role}: {', '.join(urls)}")
        else:
            report(f"WEB    -> Firecrawl search '{build_query(user, 80)}' returned {len(pages)} page(s) for {role}")
        report(f"WEB    -> {len(section)} chars of live web content added to the {role} prompt")

        return llm(role, system, f"{user}\n\n{section}", json_mode=json_mode)

    return research_llm
