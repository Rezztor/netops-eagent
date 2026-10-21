"""Web search, and what it does when there is none.

There were no tests here at all -- both DOX files recorded "no direct test
reference was found by name search" -- which is how a tool that could not work
shipped in two deployments and was still advertised to the model in every system
prompt.

The failure path matters more than the happy one. An exception raised out of a
tool reaches `Agent.handle_exception`, which the `_error_retry` plugin treats as
critical: it retries once and ends the monologue. So a search engine that is
merely absent used to destroy the conversation, which is a bad trade for the
most optional capability the agent has.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from helpers import searxng
from tools.search_engine import SearchEngine


@pytest.fixture(autouse=True)
def no_ambient_search_engine(monkeypatch):
    monkeypatch.delenv(searxng.ENV_URL, raising=False)


def _tool() -> SearchEngine:
    """A tool with just enough agent for `execute` to run."""
    agent = SimpleNamespace()

    async def handle_intervention(_=None):
        return None

    agent.handle_intervention = handle_intervention
    return SearchEngine(
        agent=agent, name="search_engine", method=None, args={}, message="", loop_data=None
    )


def _run(coro):
    return asyncio.run(coro)


# --- configuration -----------------------------------------------------------


def test_no_address_means_not_configured():
    assert searxng.is_configured() is False


def test_the_address_comes_from_the_deployment(monkeypatch):
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")
    assert searxng.is_configured() is True
    assert searxng.base_url() == "http://searxng:8080"


def test_a_trailing_slash_does_not_produce_a_double_slash(monkeypatch):
    """The URL is joined with `/search`, so the stored form has to be bare."""
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080/")
    assert searxng.base_url() == "http://searxng:8080"


# --- the tool never raises ---------------------------------------------------


def test_an_unconfigured_instance_answers_instead_of_failing():
    """The bug this file exists for. Not configured is a sentence, not a crash."""
    response = _run(_tool().execute(query="anything"))
    assert "not configured" in response.message.lower()
    assert response.break_loop is False


def test_an_unreachable_search_engine_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")

    async def boom(_query):
        raise searxng.SearchUnreachable("The search engine did not answer: refused")

    monkeypatch.setattr(searxng, "search", boom)
    response = _run(_tool().execute(query="anything"))
    assert "did not answer" in response.message
    assert response.break_loop is False


def test_a_refusal_is_reported_not_raised(monkeypatch):
    """403 is the one to expect: SearXNG serves the JSON API only when its
    settings enable that format, and refuses otherwise."""
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")

    async def refused(_query):
        raise searxng.SearchRefused("The search engine refused the query (403).")

    monkeypatch.setattr(searxng, "search", refused)
    response = _run(_tool().execute(query="anything"))
    assert "403" in response.message
    assert response.break_loop is False


# --- shaping results ---------------------------------------------------------


def test_a_result_without_content_does_not_explode(monkeypatch):
    """`item['content']` was a hard index. Some engines omit it, and one odd hit
    took the whole conversation with it through the critical-exception path."""
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")

    async def partial(_query):
        return {"results": [{"title": "T", "url": "https://example.test"}]}

    monkeypatch.setattr(searxng, "search", partial)
    response = _run(_tool().execute(query="anything"))
    assert "https://example.test" in response.message


def test_no_results_says_so_rather_than_returning_nothing(monkeypatch):
    """An empty string reads to the model as a broken tool rather than an
    answer, and invites a retry that will also return nothing."""
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")

    async def empty(_query):
        return {"results": []}

    monkeypatch.setattr(searxng, "search", empty)
    assert "no results" in _run(_tool().execute(query="anything")).message.lower()


def test_results_are_capped(monkeypatch):
    monkeypatch.setenv(searxng.ENV_URL, "http://searxng:8080")

    async def many(_query):
        return {"results": [{"title": f"t{i}", "url": f"u{i}", "content": "c"} for i in range(50)]}

    monkeypatch.setattr(searxng, "search", many)
    message = _run(_tool().execute(query="anything")).message
    assert message.count("https://") == 0
    assert len(message.split("\n\n")) == 10


# --- the client itself -------------------------------------------------------


def test_searching_with_no_address_raises_not_configured():
    """The helper is explicit too, so a caller that skips the tool's guard still
    gets a typed error rather than a connection attempt against an empty host."""
    with pytest.raises(searxng.NotConfigured):
        _run(searxng._search("anything"))


def test_the_client_has_timeouts():
    """There were none. A hung search engine hung the agent loop with it."""
    assert searxng.CONNECT_TIMEOUT > 0
    assert searxng.READ_TIMEOUT > 0
