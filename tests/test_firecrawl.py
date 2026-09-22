import json

import pytest

from firecrawl_client import FirecrawlClient, FirecrawlError, WebPage


class FakeTransport:
    """Records requests and replays scripted (status, body) responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, body, headers, timeout):
        self.requests.append(
            {
                "url": url,
                "payload": json.loads(body.decode("utf-8")),
                "headers": headers,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("No more scripted Firecrawl responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return status, json.dumps(payload).encode("utf-8")


def make_client(transport, **kwargs):
    kwargs.setdefault("api_key", "fc-test-key")
    kwargs.setdefault("backoff_seconds", 0.0)  # keep retry tests instant
    return FirecrawlClient(transport=transport, **kwargs)


def test_scrape_sends_expected_request_and_parses_markdown():
    transport = FakeTransport(
        [
            (
                200,
                {
                    "success": True,
                    "data": {
                        "markdown": "# Capacitors\n\nCharge is stored on the plates.",
                        "metadata": {"title": "Capacitor Basics", "sourceURL": "https://example.com/cap"},
                    },
                },
            )
        ]
    )

    page = make_client(transport).scrape("https://example.com/cap")

    assert isinstance(page, WebPage)
    assert page.url == "https://example.com/cap"
    assert page.title == "Capacitor Basics"
    assert "Charge is stored" in page.markdown

    request = transport.requests[0]
    assert request["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert request["payload"] == {
        "url": "https://example.com/cap",
        "formats": ["markdown"],
        "onlyMainContent": True,
    }
    assert request["headers"]["Authorization"] == "Bearer fc-test-key"


def test_search_accepts_v2_and_v1_response_shapes():
    v2_body = {
        "success": True,
        "data": {"web": [{"url": "https://a.example", "title": "A", "markdown": "alpha"}]},
    }
    pages = make_client(FakeTransport([(200, v2_body)])).search("capacitors", limit=2)
    assert [page.url for page in pages] == ["https://a.example"]
    assert pages[0].markdown == "alpha"

    v1_body = {
        "success": True,
        "data": [
            {
                "url": "https://b.example",
                "title": "B",
                "description": "beta",
                "highlights": ["first highlight", "second highlight"],
            }
        ],
    }
    pages = make_client(FakeTransport([(200, v1_body)])).search("capacitors")
    assert [page.url for page in pages] == ["https://b.example"]
    assert pages[0].markdown == "first highlight\nsecond highlight"


def test_search_asks_for_markdown_content_by_default():
    transport = FakeTransport([(200, {"success": True, "data": {"web": []}})])

    make_client(transport).search("farad", limit=3)

    payload = transport.requests[0]["payload"]
    assert payload["sources"] == ["web"]
    assert payload["scrapeOptions"]["formats"] == ["markdown"]
    assert transport.requests[0]["url"] == "https://api.firecrawl.dev/v2/search"


def test_transient_status_is_retried_then_succeeds():
    transport = FakeTransport(
        [(429, {"error": "rate limited"}), (200, {"success": True, "data": {"web": []}})]
    )

    make_client(transport, max_retries=2).search("q")

    assert len(transport.requests) == 2


def test_client_error_is_not_retried():
    transport = FakeTransport([(401, {"error": "Invalid token"})])

    with pytest.raises(FirecrawlError) as exc_info:
        make_client(transport).search("q")

    assert "Invalid token" in str(exc_info.value)
    assert len(transport.requests) == 1


def test_network_failure_is_retried_then_reported():
    transport = FakeTransport([OSError("connection refused"), OSError("connection refused")])

    with pytest.raises(FirecrawlError) as exc_info:
        make_client(transport, max_retries=1).search("q")

    assert len(transport.requests) == 2
    assert "failed after 2 attempts" in str(exc_info.value)


def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    transport = FakeTransport([])
    client = FirecrawlClient(transport=transport)

    with pytest.raises(FirecrawlError) as exc_info:
        client.scrape("https://example.com")

    assert "FIRECRAWL_API_KEY" in str(exc_info.value)
    assert transport.requests == []


def test_success_false_is_an_error():
    transport = FakeTransport([(200, {"success": False, "error": "Scrape failed"})])

    with pytest.raises(FirecrawlError) as exc_info:
        make_client(transport).scrape("https://example.com")

    assert "Scrape failed" in str(exc_info.value)


def test_unreadable_body_is_an_error():
    def broken_transport(url, body, headers, timeout):
        return 200, b"<html>not json</html>"

    with pytest.raises(FirecrawlError) as exc_info:
        make_client(broken_transport).scrape("https://example.com")

    assert "unreadable" in str(exc_info.value)


def test_shared_client_is_created_once(monkeypatch):
    import firecrawl_client

    monkeypatch.setattr(firecrawl_client, "_default_client", None)
    first = firecrawl_client.get_client()
    assert firecrawl_client.get_client() is first
