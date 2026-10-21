"""The parts of the Azure change plugin that are rules rather than flow: the CR or SR number, the risk tier, the gate's
fallback, what the executor client can and cannot say, the Settings endpoint's refusals, and that nothing in the plugin
can delete or stop anything or reach the network except through `executor_client`."""

from __future__ import annotations

import asyncio
import importlib
import re
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
import yaml

from helpers import plugins
from plugins._ea_azure_change.api import azure_change_config
from plugins._ea_azure_change.helpers import executor_client, records, tier

PLUGIN = Path(__file__).resolve().parents[1]
SOURCES = [p for p in PLUGIN.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts]


@pytest.fixture
def default_rails(monkeypatch):
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: {})


# --- the number ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,ok", [("CR-12345", True), ("sr-0001234", True), ("SR12345", True), (" cr-1234567890 ", True), ("CR-123", False),
                                      ("CR-12345678901", False), ("", False), (None, False), ("CR 12345", False), ("CR-12345 ", True),
                                      ("XCR-12345", False), ("CR-12345\n", True), ("CR-1\n2345", False), ("CR-１２３４５", False), ("CR-" + "9" * 38, False)])
def test_the_default_pattern_is_a_full_match_of_cr_or_sr_and_digits(default_rails, value, ok):
    assert (records.reference_problem(value) is None) == ok


def test_a_pattern_that_would_accept_anything_or_cannot_run_is_refused(default_rails):
    for bad in ("", "   ", ".*", "(", "[0-9]*", "x" * 101, None):
        assert records.pattern_problem(bad), bad
    assert records.pattern_problem("PO-[0-9]{6}") is None


def test_a_broken_saved_pattern_blocks_every_approval_it_never_opens_the_gate(monkeypatch):
    monkeypatch.setattr(records, "rails", lambda agent=None: {**records.RAIL_DEFAULTS, "reference_pattern": ".*"})
    assert "matches an empty number" in records.reference_problem("CR-12345")


def test_the_application_defaults_equal_the_shipped_default_config_and_have_no_delete_setting():
    shipped = yaml.safe_load((PLUGIN / "default_config.yaml").read_text())["guardrails"]
    assert {k: shipped[k] for k in records.RAIL_DEFAULTS} == records.RAIL_DEFAULTS
    assert not [k for k in shipped if "delet" in k or "destroy" in k or "stop" in k]


# --- the tier ------------------------------------------------------------------------------------------------------------

def test_the_tier_is_high_for_free_code_many_resources_cost_unknown_price_or_a_template_not_marked_low():
    rails = records.RAIL_DEFAULTS
    low = {"risk": "low"}
    plan = lambda add=1, monthly=0.0, unknown=(): {"summary": {"add": add}, "cost": {"monthly_usd": monthly, "unknown": list(unknown)}}  # noqa: E731
    assert tier.decide(low, plan(), rails) == ("change-low", [])
    assert tier.decide(low, plan(), rails, free_code=True)[0] == "change-high"
    assert tier.decide(low, plan(add=11), rails)[0] == "change-high"
    assert tier.decide(low, plan(monthly=20.01), rails)[0] == "change-high"
    assert tier.decide(low, plan(unknown=["azurerm_x.y"]), rails)[0] == "change-high"
    assert tier.decide({"risk": "high"}, plan(), rails)[0] == "change-high" and tier.decide(None, plan(), rails)[0] == "change-high"
    assert tier.LOW != "confirm" and tier.HIGH != "confirm"


# --- the gate's fallback ----------------------------------------------------------------------------------------------------

def test_a_missing_static_gate_refuses_everything_it_never_reads_as_passing(monkeypatch):
    monkeypatch.setitem(sys.modules, "cloud_executor.gate", None)
    name = "plugins._ea_azure_change.helpers.gate"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    assert module.check_code({"main.tf": ""}) and module.check_plan({}, {}, {}, set())[1]


# --- the executor client ----------------------------------------------------------------------------------------------------

def test_the_client_has_no_destroy_and_only_admin_paths_go_through_admin(monkeypatch):
    with pytest.raises(executor_client.ExecutorError, match="no plan mode"):
        executor_client.plan(None, source={}, workspace={}, change_id="x", actor="a", mode="destroy")
    with pytest.raises(executor_client.ExecutorError, match="Only /v1/admin/"):
        executor_client.admin(None, "POST", "/v1/apply")
    assert not [n for n in dir(executor_client) if re.search(r"destroy|delete|stop", n, re.I)]


def test_the_client_talks_to_a_unix_socket_with_the_right_token_and_no_token_means_no_request(tmp_path, monkeypatch):
    sock = str(tmp_path / "cloud.sock")
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            body = b'{"status": "applied"}' if self.path.startswith("/v1/jobs/") else b'{"error": "no"}'
            self.send_response(200 if self.path.startswith("/v1/jobs/") else 403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def address_string(self):
            return "unix"

        def log_message(self, *args):
            pass

    class Server(socketserver.UnixStreamServer):
        pass

    server = Server(sock, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: {"executor_url": f"unix://{sock}", "timeout_seconds": 5})
    try:
        monkeypatch.delenv("REZZTOR_CLOUD_JOB_TOKEN", raising=False)
        monkeypatch.delenv("REZZTOR_CLOUD_JOB_TOKEN_FILE", raising=False)
        monkeypatch.setattr(executor_client.dotenv, "get_dotenv_value", lambda key, default=None: "")
        with pytest.raises(executor_client.NotConfigured):
            executor_client.job(None, "j1")
        assert seen == []
        monkeypatch.setattr(executor_client.dotenv, "get_dotenv_value", lambda key, default=None: "job-token")
        assert executor_client.job(None, "../j1")["status"] == "applied"
        assert seen == [("/v1/jobs/j1", "Bearer job-token")]
        with pytest.raises(executor_client.ExecutorRefused, match="no"):
            executor_client.health(None)
    finally:
        server.shutdown()
        server.server_close()


# --- the Settings endpoint ----------------------------------------------------------------------------------------------------

def config(**body):
    return asyncio.run(azure_change_config.AzureChangeConfig(None, None).process(body, None))


def test_the_settings_endpoint_refuses_a_bad_pattern_and_any_delete_setting_before_it_calls_the_executor(monkeypatch, default_rails):
    calls = []
    monkeypatch.setattr(executor_client, "admin", lambda *a, **k: calls.append(a) or {})
    for bad in ({"reference_pattern": ".*"}, {"reference_pattern": "("}, {"deletes_allowed": True}, {"max_deletes": 5}, {"document_valid_days": 31},
                {"document_valid_days": 0}, "x"):
        assert not config(action="save_guardrails", guardrails=bad)["ok"], bad
    assert calls == []


def test_the_settings_endpoint_saves_to_the_executor_first_then_the_copy_the_app_reads_and_returns_no_key(monkeypatch, default_rails):
    stored = {"reference_pattern": "PO-[0-9]{6}", "usd_month_per_change": 250, "admin_token": "leak", "private_key": "leak"}
    answers = {("GET", "/v1/admin/guardrails"): {"usd_month_per_change": 250}, ("PUT", "/v1/admin/guardrails"): stored,
               ("GET", "/v1/admin/keys"): {"planner": {"set": True, "thumbprint": "AB"}, "passphrase_set": True, "client_secret": "leak"},
               ("POST", "/v1/admin/keys/planner"): {"ok": True, "public_certificate": "PEM", "thumbprint": "AB", "expires_on": "2027-10-03", "private_key": "leak"},
               ("POST", "/v1/admin/test"): {"role_ok": False, "reason": "holds Contributor", "token": "leak"}}
    saved, events = [], []
    monkeypatch.setattr(executor_client, "admin", lambda agent, method, path, body=None: answers[(method, path)])
    monkeypatch.setattr(azure_change_config.AzureChangeConfig, "_save_local", staticmethod(lambda rails: saved.append(rails)))
    monkeypatch.setattr(azure_change_config.audit, "emit", lambda event, **kw: events.append((event, kw)))
    done = config(action="save_guardrails", guardrails={"reference_pattern": "PO-[0-9]{6}"})
    assert done["ok"] and saved and saved[0]["reference_pattern"] == "PO-[0-9]{6}" and "admin_token" not in str(done) and "leak" not in str(done)
    keys = config(action="keys")
    assert keys["passphrase_set"] is True and "leak" not in str(keys)
    made = config(action="make_key", role="planner", tenant_id="t", client_id="c")
    assert set(made) == {"ok", "public_certificate", "thumbprint", "expires_on"}
    assert not config(action="make_key", role="root", tenant_id="t", client_id="c")["ok"]
    checked = config(action="test")
    assert checked["role_ok"] is False and "leak" not in str(checked)
    assert [e for e, _ in events] == ["azure.guardrails_saved", "azure.key_made", "azure.role_checked"]
    assert events[0][1]["changed"] == ["reference_pattern"]


def test_the_settings_endpoint_says_so_when_the_executor_cannot_be_reached(monkeypatch, default_rails):
    def down(*a, **k):
        raise executor_client.ExecutorUnreachable("The cloud executor did not answer: refused")

    monkeypatch.setattr(executor_client, "admin", down)
    answer = config(action="get")
    assert not answer["ok"] and "did not answer" in answer["error"] and answer["local"]["reference_pattern"]


# --- nothing else reaches the network, nothing deletes ---------------------------------------------------------------------------

def test_only_the_executor_client_reaches_a_socket_or_an_http_client():
    reaching = re.compile(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp|http\.client|socket|websocket)\b", re.M)
    offenders = [p.name for p in SOURCES if p.name != "executor_client.py" and reaching.search(p.read_text())]
    assert offenders == []


def test_no_code_in_the_plugin_can_ask_for_a_removal_or_a_stop():
    """Docstrings and prompts say what is never done; what is scanned is a string literal or a call that would do it."""
    verbs = r"destroy|DELETE|delete|stop|deallocate|powerOff|restart|forget"
    asks = re.compile(rf"[\"']({verbs})[\"']|\.delete\(|requests\.delete")
    found = [f"{p.name}:{n}: {line.strip()}" for p in SOURCES for n, line in enumerate(p.read_text().splitlines(), 1) if asks.search(line)]
    assert found == []
