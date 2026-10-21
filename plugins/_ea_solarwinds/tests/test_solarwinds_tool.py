"""The tool's job is routing: turn each of this plugin's own exceptions into a
message the agent can read and act on, and never let SolarWinds' internals or
credentials reach the transcript.
"""

from __future__ import annotations

import asyncio

import pytest

from plugins._ea_solarwinds.helpers import capabilities, client, policy
from plugins._ea_solarwinds.helpers import config as plugin_config
from plugins._ea_solarwinds.tools.solarwinds_lookup import SolarWindsLookup


class _Context:
    def __init__(self, ctx_id="ctx-1"):
        self.id = ctx_id
        self._data = {}

    def get_data(self, key):
        return self._data.get(key)

    def set_data(self, key, value):
        self._data[key] = value


class _Agent:
    def __init__(self, **args):
        self.context = _Context()
        self.args = args


def _tool(**args):
    tool = SolarWindsLookup.__new__(SolarWindsLookup)
    tool.agent = _Agent(**args)
    tool.args = args
    return tool


def _run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(policy, "check_and_record", lambda ctx_id: None)


def test_not_configured_is_reported_plainly_not_as_a_stack_trace(monkeypatch):
    monkeypatch.setattr(plugin_config, "is_configured", lambda: False)
    response = _run(_tool(capability="node_status", node="sw1"))
    assert "not configured" in response.message
    assert response.break_loop is False


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(plugin_config, "is_configured", lambda: True)


def test_an_unknown_capability_is_reported_by_name(configured):
    response = _run(_tool(capability="delete_everything"))
    assert "not a capability" in response.message


def test_a_missing_required_argument_is_reported(configured):
    response = _run(_tool(capability="node_status"))
    assert "'node' is required" in response.message


def test_rate_limited_stops_before_any_network_call(configured, monkeypatch):
    called = []
    monkeypatch.setattr(client, "query", lambda *a, **k: called.append(1))

    def refuse(ctx_id):
        raise policy.RateLimited("This conversation has already made 20 calls in the last minute, which is the limit.")

    monkeypatch.setattr(policy, "check_and_record", refuse)
    response = _run(_tool(capability="node_status", node="sw1"))
    assert "limit" in response.message
    assert called == []


def test_capability_unavailable_names_the_capability_not_the_swis_error(configured, monkeypatch):
    def raise_unavailable(swql, params):
        raise client.CapabilityUnavailable(
            "Source entity [Orion.Netflow.FlowsByIP] not found in catalog."
        )

    monkeypatch.setattr(client, "query", raise_unavailable)
    response = _run(_tool(capability="netflow_by_ip", ip="10.0.0.1"))
    assert "netflow_by_ip" in response.message
    assert "isn't enabled on this SolarWinds instance" in response.message
    # The raw SWIS error text must not reach the transcript.
    assert "catalog" not in response.message
    assert "Orion.Netflow" not in response.message


def test_a_connection_failure_is_shown_without_the_credentials(configured, monkeypatch):
    def raise_failed(swql, params):
        raise client.ConnectionFailed("SolarWinds rejected the configured credentials.")

    monkeypatch.setattr(client, "query", raise_failed)
    response = _run(_tool(capability="node_status", node="sw1"))
    assert "Could not read from SolarWinds" in response.message


def test_a_successful_call_reports_the_rows(configured, monkeypatch):
    monkeypatch.setattr(client, "query", lambda swql, params: [{"Caption": "sw1", "Status": "Up"}])
    response = _run(_tool(capability="node_status", node="sw1"))
    assert "1 result" in response.message
    assert "sw1" in response.message
    assert "Up" in response.message


def test_a_successful_call_with_no_rows_says_so_plainly(configured, monkeypatch):
    monkeypatch.setattr(client, "query", lambda swql, params: [])
    response = _run(_tool(capability="node_status", node="sw1"))
    assert "no results" in response.message


def test_the_built_query_and_params_are_what_reach_the_client(configured, monkeypatch):
    seen = {}

    def fake_query(swql, params):
        seen["swql"] = swql
        seen["params"] = params
        return []

    monkeypatch.setattr(client, "query", fake_query)
    _run(_tool(capability="node_search", fragment="core"))
    expected = capabilities.build("node_search", {"fragment": "core"})
    assert seen["swql"] == expected.swql
    assert seen["params"] == expected.params


def test_the_rate_limit_key_is_the_user_facing_context_not_a_parallel_worker(configured, monkeypatch):
    """A tool dispatched through `parallel` runs in a throwaway context that is
    deleted the moment the job returns. Counting against that worker's id would
    make the cap meaningless -- every call would look like a fresh
    conversation, because the id counted against no longer exists a moment
    later. `user_facing_context`'s own walk is tested in
    tests/test_parallel_user_facing_context.py; what matters here is only that
    this tool actually calls it, rather than reading `self.agent.context.id`
    directly the way a tool without this concern would.
    """
    from agent import AgentContext
    from helpers.parallel_tools import PARALLEL_WORKER_PARENT_CONTEXT_KEY

    monkeypatch.setattr(client, "query", lambda swql, params: [])

    parent = _Context("real-conversation")
    tool = _tool(capability="node_status", node="sw1")
    tool.agent.context.set_data(PARALLEL_WORKER_PARENT_CONTEXT_KEY, parent.id)

    monkeypatch.setattr(AgentContext, "get", staticmethod(lambda cid: parent if cid == parent.id else None))

    seen = {}
    monkeypatch.setattr(policy, "check_and_record", lambda ctx_id: seen.setdefault("ctx", ctx_id))

    _run(tool)

    assert seen["ctx"] == "real-conversation"
    assert seen["ctx"] != tool.agent.context.id
