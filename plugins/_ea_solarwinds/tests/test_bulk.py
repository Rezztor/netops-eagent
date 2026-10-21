"""Bulk node fetch against the fake SWIS server: paging, truncation, tolerance,
failure modes, and the ADR 0026 guarantees (fixed templates, bound values,
read-only, invisible to the agent)."""

from __future__ import annotations

import itertools
import re

import pytest
import requests

from plugins._ea_solarwinds.helpers import bulk, capabilities, client, policy
from plugins._ea_solarwinds.tests.fake_swis import QUERY_PATH, FakeSwis, generated_nodes

FIELDS = {
    "node_id", "caption", "dns", "sys_name", "ip_address", "vendor", "machine_type", "software_version",
    "sys_object_id", "location", "status", "serial", "last_poll",
}


@pytest.fixture
def swis(monkeypatch):
    """The fake server, with the client pointed at it (https -> http: the fake
    has no certificate) and the rate ledger replaced by a counter."""
    real_post = requests.post
    monkeypatch.setattr(requests, "post", lambda url, **kw: real_post(url.replace("https://", "http://", 1), **kw))
    monkeypatch.setattr(policy, "check_and_record", lambda key: None)
    with FakeSwis() as server:
        monkeypatch.setattr(
            client, "resolve_connection",
            lambda: client.Connection("127.0.0.1", server.port, "svc", "secret", True, 0.5),
        )
        yield server


def _known_templates() -> set[str]:
    texts = {
        capabilities.build("nodes_page", {"after": 0, "page": 1, "omit": omit}, internal=True).swql
        for n in range(len(capabilities.NODE_OPTIONAL_COLUMNS) + 1)
        for omit in itertools.combinations(capabilities.NODE_OPTIONAL_COLUMNS, n)
    }
    for name in ("node_serials_page", "node_serials_ncm_page"):
        texts.add(capabilities.build(name, {"after": 0, "upto": 1}, internal=True).swql)
    return texts


def test_hand_fixture_shapes_and_values(swis):
    result = bulk.fetch_nodes()
    assert result.complete and not result.truncated and result.missing_columns == []
    by_id = {n["node_id"]: n for n in result.nodes}
    assert len(result.nodes) == 12 and all(set(n) == FIELDS for n in result.nodes)
    assert by_id["1"]["serial"] == "FOC1111AAAA" and by_id["1"]["dns"] == "core-sw1.corp.example"
    assert by_id["2"]["caption"] == "CORE-SW2"  # raw: normalising is the inventory's job
    assert by_id["6"]["serial"] == "JPE6666FFFF"  # only NCM has it
    assert by_id["7"]["serial"] is None and by_id["7"]["vendor"] == ""
    assert by_id["11"]["status"] == "down" and by_id["11"]["location"] == ""
    assert by_id["8"]["ip_address"] == by_id["9"]["ip_address"]  # duplicate IP kept, not merged
    assert by_id["1"]["last_poll"] == "2026-09-30T10:01:00+00:00"
    assert by_id["1"]["software_version"] == "17.9.4a"


def test_paging_is_keyset_ordered_and_complete(swis):
    swis.nodes = generated_nodes(450)
    result = bulk.fetch_nodes(page_size=200)
    assert [int(n["node_id"]) for n in result.nodes] == list(range(1, 451))
    pages = [r["params"] for r in swis.received if "FROM Orion.Nodes" in r["query"]]
    assert [(p["after"], p["page"]) for p in pages] == [(0, 200), (200, 200), (400, 200)]
    assert "OFFSET" not in " ".join(r["query"] for r in swis.received).upper()


def test_truncation_at_max_nodes(swis):
    swis.nodes = generated_nodes(450)
    result = bulk.fetch_nodes(max_nodes=300, page_size=200)
    assert len(result.nodes) == 300 and result.truncated and not result.complete
    exact = bulk.fetch_nodes(max_nodes=450, page_size=200)
    assert len(exact.nodes) == 450 and exact.complete and not exact.truncated


def test_a_missing_node_column_is_dropped_and_reported(swis):
    swis.missing_columns = {"IOSVersion"}
    result = bulk.fetch_nodes()
    assert len(result.nodes) == 12 and result.missing_columns == ["Orion.Nodes.IOSVersion"]
    assert {n["software_version"] for n in result.nodes} == {""}
    assert result.nodes[0]["caption"] == "core-sw1" and result.nodes[0]["sys_object_id"]


def test_a_missing_serial_source_is_reported_and_the_other_still_fills(swis):
    swis.missing_entities = {"Orion.HardwareHealth.HardwareInfo"}
    result = bulk.fetch_nodes()
    by_id = {n["node_id"]: n for n in result.nodes}
    assert result.missing_columns == ["Orion.HardwareHealth.HardwareInfo.ServiceTag"]
    assert by_id["1"]["serial"] is None and by_id["6"]["serial"] == "JPE6666FFFF"
    swis.missing_entities = {"Orion.HardwareHealth.HardwareInfo", "Cirrus.EntityPhysical"}
    result = bulk.fetch_nodes()
    assert all(n["serial"] is None for n in result.nodes) and len(result.missing_columns) == 2


def test_a_missing_source_is_not_asked_again_on_later_pages(swis):
    swis.nodes = generated_nodes(450)
    swis.missing_entities = {"Cirrus.EntityPhysical"}
    bulk.fetch_nodes(page_size=200)
    assert sum("Cirrus.EntityPhysical" in r["query"] for r in swis.received) == 1


@pytest.mark.parametrize("setup, message", [
    (lambda s: setattr(s, "status_all", 401), "rejected"),
    (lambda s: setattr(s, "fail_node_page", (2, 500)), "node read failed"),
    (lambda s: setattr(s, "delay", 1.5), "did not answer"),
    (lambda s: setattr(s, "malformed", True), "not JSON"),
    (lambda s: setattr(s, "status_all", 500), "node read failed"),
])
def test_failures_raise_a_clear_error_and_return_nothing(swis, setup, message):
    swis.nodes = generated_nodes(450)
    setup(swis)
    with pytest.raises(bulk.BulkFetchError, match=message):
        bulk.fetch_nodes(page_size=200)


def test_a_server_error_on_the_serial_query_is_fatal_not_tolerated(swis):
    swis.fail_serials = 500
    with pytest.raises(bulk.BulkFetchError, match="serial read failed"):
        bulk.fetch_nodes()


def test_rate_limit_that_never_clears_fails_the_run(swis, monkeypatch):
    def limited(key):
        raise policy.RateLimited("slow down")

    monkeypatch.setattr(policy, "check_and_record", limited)
    monkeypatch.setattr(bulk.time, "sleep", lambda s: None)
    with pytest.raises(bulk.BulkFetchError, match="slow down"):
        bulk.fetch_nodes()
    assert swis.received == []


def test_every_query_the_server_received_is_a_known_template_and_read_only(swis):
    swis.nodes = generated_nodes(450)
    bulk.fetch_nodes(page_size=200)
    swis.missing_columns = {"Location"}
    swis.missing_entities = {"Cirrus.EntityPhysical"}
    bulk.fetch_nodes(page_size=200)
    known = _known_templates()
    assert swis.received
    for request in swis.received:
        assert request["path"] == QUERY_PATH
        assert request["query"] in known, request["query"]
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|EXEC|INVOKE|CREATE|ALTER)\b", request["query"], re.I)
        assert all(isinstance(v, int) for v in request["params"].values())
        assert request["auth"].startswith("Basic ")


def test_internal_capabilities_are_invisible_to_the_agent_surface():
    internal = {"nodes_page", "node_serials_page", "node_serials_ncm_page"}
    assert internal.isdisjoint(capabilities.names())
    assert internal.isdisjoint(c["name"] for c in capabilities.describe())
    for name in internal:
        with pytest.raises(capabilities.UnknownCapability):
            capabilities.build(name, {"after": 0, "page": 1, "upto": 1})


def test_internal_arguments_cannot_reach_the_query_text():
    attack = "0; DROP TABLE Orion.Nodes --"
    for args in ({"after": attack, "page": 1}, {"after": 0, "page": attack}, {"after": 0, "page": 0}, {"after": 0, "page": 10**6},
                 {"after": 0, "page": 1, "omit": [attack]}, {"after": True, "page": 1}):
        with pytest.raises(capabilities.CapabilityError):
            capabilities.build("nodes_page", args, internal=True)
    for name in ("node_serials_page", "node_serials_ncm_page"):
        with pytest.raises(capabilities.CapabilityError):
            capabilities.build(name, {"after": attack, "upto": 1}, internal=True)
    built = capabilities.build("nodes_page", {"after": 7, "page": 200}, internal=True)
    assert "@after" in built.swql and "@page" in built.swql and built.params == {"after": 7, "page": 200}
