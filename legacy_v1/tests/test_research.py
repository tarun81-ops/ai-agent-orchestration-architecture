import pytest

from firecrawl_client import FirecrawlError, WebPage
from research import (
    RESEARCH_HEADER,
    build_query,
    build_research_llm,
    extract_urls,
    format_research_section,
)

STEP_PROMPT = (
    "Original Task:\nExplain how a capacitor stores charge\n\n"
    "Current Step: step_1 (researcher)\n"
    "Instruction:\nResearch capacitor charge storage.\n\n"
    "Dependency Outputs:\n(none)"
)

REVISE_PROMPT = STEP_PROMPT + (
    "\n\nPrevious Output for Revision:\nthin draft\n\n"
    "Reviewer Feedback:\n"
    "- Problem: not enough detail\n"
    "- Recommended Fix: add the physics and cite sources\n\n"
    "Instruction for Revision:\n"
    "Please revise your previous output based on the reviewer's feedback."
)


class FakeClient:
    """Stand-in for FirecrawlClient: records calls, returns canned pages."""

    def __init__(self, pages=None, error=None, search_results=None):
        self.pages = list(pages or [])
        self.error = error
        self.search_results = search_results
        self.scrapes = []
        self.searches = []

    def scrape(self, url):
        self.scrapes.append(url)
        if self.error is not None:
            raise self.error
        if self.pages:
            return self.pages.pop(0)
        return WebPage(url=url, title=f"Page {url}", markdown=f"content of {url}")

    def search(self, query, limit=3):
        self.searches.append((query, limit))
        if self.error is not None:
            raise self.error
        if self.search_results is not None:
            return list(self.search_results)
        return [WebPage(url="https://example.com/hit", title="Hit", markdown="searched content")]


class FakeLLM:
    def __init__(self, response="done"):
        self.response = response
        self.calls = []

    def __call__(self, role, system, user, json_mode=False):
        self.calls.append({"role": role, "system": system, "user": user, "json_mode": json_mode})
        return self.response


def test_researcher_prompt_gets_searched_web_content():
    client = FakeClient()
    llm = FakeLLM()
    notes = []
    research = build_research_llm(llm, client=client, notify=notes.append)

    result = research("researcher", "sys", STEP_PROMPT)

    assert result == "done"
    assert client.searches == [("Research capacitor charge storage", 3)]
    assert client.scrapes == []

    sent = llm.calls[0]
    assert sent["user"].startswith(STEP_PROMPT)  # original prompt is preserved
    assert RESEARCH_HEADER in sent["user"]
    assert "searched content" in sent["user"]
    assert any("Firecrawl search" in note for note in notes)


def test_urls_in_the_step_are_scraped_instead_of_searched():
    prompt = STEP_PROMPT + "\n\nCompare https://example.com/a with https://example.com/b."
    client = FakeClient()
    llm = FakeLLM()
    research = build_research_llm(llm, client=client)

    research("researcher", "sys", prompt)

    assert client.scrapes == ["https://example.com/a", "https://example.com/b"]
    assert client.searches == []
    assert "content of https://example.com/a" in llm.calls[0]["user"]


def test_max_urls_limits_how_many_pages_are_scraped():
    prompt = STEP_PROMPT + " See https://a.example https://b.example https://c.example"
    client = FakeClient()
    research = build_research_llm(FakeLLM(), client=client, max_urls=1)

    research("researcher", "sys", prompt)

    assert client.scrapes == ["https://a.example"]


def test_other_roles_pass_through_untouched():
    client = FakeClient()
    llm = FakeLLM()
    research = build_research_llm(llm, client=client)

    research("writer", "sys", STEP_PROMPT)
    research("reviewer", "sys", STEP_PROMPT, json_mode=True)

    assert client.searches == []
    assert client.scrapes == []
    assert llm.calls[0]["user"] == STEP_PROMPT
    assert llm.calls[1]["user"] == STEP_PROMPT
    assert llm.calls[1]["json_mode"] is True


def test_json_mode_is_preserved_for_the_researcher():
    llm = FakeLLM(response={"ok": True})
    research = build_research_llm(llm, client=FakeClient())

    research("researcher", "sys", STEP_PROMPT, json_mode=True)

    assert llm.calls[0]["json_mode"] is True


def test_firecrawl_failure_degrades_to_a_plain_prompt():
    client = FakeClient(error=FirecrawlError("Set the FIRECRAWL_API_KEY environment variable first"))
    llm = FakeLLM()
    notes = []
    research = build_research_llm(llm, client=client, notify=notes.append)

    assert research("researcher", "sys", STEP_PROMPT) == "done"

    assert llm.calls[0]["user"] == STEP_PROMPT
    assert any("web research skipped" in note for note in notes)


def test_search_without_results_degrades_to_a_plain_prompt():
    llm = FakeLLM()
    notes = []
    research = build_research_llm(llm, client=FakeClient(search_results=[]), notify=notes.append)

    research("researcher", "sys", STEP_PROMPT)

    assert llm.calls[0]["user"] == STEP_PROMPT
    assert any("nothing usable" in note for note in notes)


def test_missing_key_degrades_with_the_real_client(monkeypatch):
    """No client injected and no key: the run must continue without web research."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    llm = FakeLLM()
    notes = []
    research = build_research_llm(llm, notify=notes.append)

    research("researcher", "sys", STEP_PROMPT)

    assert llm.calls[0]["user"] == STEP_PROMPT
    assert any("FIRECRAWL_API_KEY" in note for note in notes)

def test_repeated_prompts_reuse_the_cache():
    client = FakeClient()
    research = build_research_llm(FakeLLM(), client=client)

    research("researcher", "sys", STEP_PROMPT)
    research("researcher", "sys", STEP_PROMPT)  # e.g. a revise round re-runs the step

    assert len(client.searches) == 1


def test_urls_already_scraped_are_not_scraped_twice():
    prompt = STEP_PROMPT + "\nSee https://example.com/once"
    client = FakeClient()
    research = build_research_llm(FakeLLM(), client=client)

    research("researcher", "sys", prompt)
    research("researcher", "sys", prompt)

    assert client.scrapes == ["https://example.com/once"]


def test_an_already_enriched_prompt_is_not_enriched_again():
    client = FakeClient()
    research = build_research_llm(FakeLLM(), client=client)

    research("researcher", "sys", f"{STEP_PROMPT}\n\n{RESEARCH_HEADER}\nsources already here")

    assert client.searches == []
    assert client.scrapes == []


def test_revision_prompt_keeps_the_original_step_query():
    assert build_query(REVISE_PROMPT) == "Research capacitor charge storage"


def test_build_query_falls_back_to_the_task():
    assert build_query("Original Task:\nExplain capacitors\n") == "Explain capacitors"
    assert build_query("just a task string") == "just a task string"
    assert build_query("") == ""


def test_extract_urls_dedupes_and_trims_punctuation():
    text = (
        "See https://example.com/a. Also https://example.com/a and "
        "(https://b.example/x), plus https://c.example"
    )

    assert extract_urls(text) == [
        "https://example.com/a",
        "https://b.example/x",
        "https://c.example",
    ]
    assert extract_urls(text, limit=2) == ["https://example.com/a", "https://b.example/x"]
    assert extract_urls("no links here") == []


def test_format_research_section_includes_sources_and_titles():
    pages = [
        WebPage(url="https://a.example", title="A", markdown="alpha body"),
        WebPage(url="https://b.example", title="", markdown="beta body"),
    ]

    section = format_research_section(pages, max_chars=1000)

    assert section.startswith(RESEARCH_HEADER)
    assert "https://a.example" in section and "alpha body" in section
    assert "### https://b.example" in section and "beta body" in section  # title falls back to URL


def test_format_research_section_truncates_long_pages():
    pages = [
        WebPage(url="https://a.example", title="A", markdown="x" * 500),
        WebPage(url="https://b.example", title="B", markdown="y" * 500),
    ]

    section = format_research_section(pages, max_chars=120)

    assert "[truncated]" in section
    assert "https://a.example" in section
    assert "https://b.example" not in section  # the budget is already spent on the first page


def test_format_research_section_returns_empty_when_nothing_is_usable():
    assert format_research_section([]) == ""
    assert format_research_section([WebPage(url="https://a.example")]) == ""
