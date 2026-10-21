"""The one guarantee everything else in this plugin rests on: this module can
read from SolarWinds and cannot write to it.
"""

from __future__ import annotations

import pytest

from plugins._ea_solarwinds.helpers import client


def _module_functions():
    return {
        name
        for name in dir(client)
        if callable(getattr(client, name)) and not name.startswith("_")
    }


def test_there_is_no_invoke_method_anywhere_in_this_module():
    """The defining property. `/Invoke/{entity}/{verb}` is how alerts get
    acknowledged and properties get edited in SolarWinds. Nothing here may be
    able to construct a request to it, under any name."""
    names = " ".join(_module_functions()).lower()
    assert "invoke" not in names


def test_query_is_the_only_function_that_reaches_the_network(monkeypatch):
    """Every other function in the module either builds inputs to query() or
    reports on its outputs. If a second network-reaching function appears
    here, this test should be the one that catches it."""
    import requests

    called = []
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: called.append((a, k)) or _ok_response([])
    )
    monkeypatch.setattr(
        client, "resolve_connection",
        lambda: client.Connection("h", 17778, "u", "p", True, 15),
    )
    client.query("SELECT 1 FROM Orion.Nodes")
    assert len(called) == 1


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _ok_response(rows):
    return _Response(200, {"results": rows})


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(
        client, "resolve_connection",
        lambda: client.Connection("orion.example.com", 17778, "svc", "secret", True, 15),
    )


def test_a_successful_query_returns_its_rows(connected, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok_response([{"Caption": "sw1"}]))
    assert client.query("SELECT Caption FROM Orion.Nodes") == [{"Caption": "sw1"}]


def test_the_request_is_a_post_to_the_query_endpoint_only(connected, monkeypatch):
    import requests

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _ok_response([])

    monkeypatch.setattr(requests, "post", fake_post)
    client.query("SELECT 1 FROM Orion.Nodes", {"x": "y"})
    assert seen["url"].endswith("/SolarWinds/InformationService/v3/Json/Query")
    assert seen["url"].startswith("https://orion.example.com:17778/")
    assert seen["kwargs"]["json"] == {"query": "SELECT 1 FROM Orion.Nodes", "parameters": {"x": "y"}}


def test_credentials_are_sent_as_http_basic_auth_not_in_the_body(connected, monkeypatch):
    import requests

    seen = {}
    monkeypatch.setattr(
        requests, "post",
        lambda url, **kwargs: seen.update(kwargs) or _ok_response([]),
    )
    client.query("SELECT 1 FROM Orion.Nodes")
    assert seen["auth"] == ("svc", "secret")
    assert "secret" not in str(seen["json"])


def test_no_connection_configured_refuses_before_any_network_call(monkeypatch):
    import requests

    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1))

    def fail():
        raise client.NotConfigured("missing host, username, password")

    monkeypatch.setattr(client, "resolve_connection", fail)
    with pytest.raises(client.NotConfigured):
        client.query("SELECT 1 FROM Orion.Nodes")
    assert called == []


def test_resolve_connection_names_every_missing_field(monkeypatch):
    from plugins._ea_solarwinds.helpers import config as plugin_config
    from helpers import dotenv

    monkeypatch.setattr(plugin_config, "get_config", lambda: {
        "host": "", "port": 17778, "username": "", "verify_tls": True, "timeout_seconds": 15,
    })
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "")
    with pytest.raises(client.NotConfigured, match="host, username, password"):
        client.resolve_connection()


def test_an_unrecognised_entity_is_reported_as_capability_unavailable(connected, monkeypatch):
    """The evidence for this shape is real -- a SolarWinds forum thread quotes
    "Source entity [Orion.AlertHistory] not found in catalog" verbatim -- but
    it has not been exercised against a live instance from this codebase. This
    test pins the *pattern* the client reacts to; live verification is a
    Phase 1 checklist item, not something this test can stand in for."""
    import requests

    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _Response(
            400, {"Message": "Source entity [Orion.Netflow.FlowsByIP] not found in catalog."}
        ),
    )
    with pytest.raises(client.CapabilityUnavailable):
        client.query("SELECT 1 FROM Orion.Netflow.FlowsByIP")


def test_an_ordinary_query_error_is_not_mistaken_for_a_missing_module(connected, monkeypatch):
    import requests

    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _Response(400, {"Message": "Syntax error near 'WHRE'."}),
    )
    with pytest.raises(client.ConnectionFailed):
        client.query("SELECT 1 FROM Orion.Nodes WHRE x")


def test_wrong_credentials_are_reported_plainly(connected, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(401, {}))
    with pytest.raises(client.ConnectionFailed, match="rejected"):
        client.query("SELECT 1 FROM Orion.Nodes")


def test_a_timeout_is_reported_without_the_underlying_stack(connected, monkeypatch):
    import requests

    def raise_timeout(*a, **k):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(requests, "post", raise_timeout)
    with pytest.raises(client.ConnectionFailed, match="did not answer"):
        client.query("SELECT 1 FROM Orion.Nodes")


def test_a_certificate_failure_names_verify_tls_as_the_way_out(connected, monkeypatch):
    import requests

    def raise_ssl(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(requests, "post", raise_ssl)
    with pytest.raises(client.ConnectionFailed, match="verify_tls"):
        client.query("SELECT 1 FROM Orion.Nodes")


def test_a_redirect_is_never_followed(connected, monkeypatch):
    """A redirect would re-send the Basic credentials, and a POST, to wherever it points."""
    import requests

    seen = {}
    monkeypatch.setattr(requests, "post", lambda url, **kwargs: seen.update(kwargs) or _Response(307, {}))
    with pytest.raises(client.ConnectionFailed, match="redirect"):
        client.query("SELECT 1 FROM Orion.Nodes")
    assert seen["allow_redirects"] is False


def test_verify_tls_is_actually_passed_to_the_request(connected, monkeypatch):
    import requests

    seen = {}
    monkeypatch.setattr(
        requests, "post",
        lambda url, **kwargs: seen.update(kwargs) or _ok_response([]),
    )
    client.query("SELECT 1 FROM Orion.Nodes")
    assert seen["verify"] is True


def test_a_non_json_response_does_not_crash_the_caller(connected, monkeypatch):
    import requests

    class _BadJson(_Response):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(requests, "post", lambda *a, **k: _BadJson(200, ""))
    with pytest.raises(client.ConnectionFailed):
        client.query("SELECT 1 FROM Orion.Nodes")


def test_a_response_with_no_results_key_is_an_empty_list_not_a_crash(connected, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(200, {}))
    assert client.query("SELECT 1 FROM Orion.Nodes") == []
