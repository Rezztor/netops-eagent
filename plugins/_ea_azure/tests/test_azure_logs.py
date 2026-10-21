"""Log queries: the HTTP call and the answer (helpers/logs.py), the named log reads, the second certificate, the settings
and, through `azure_read`, the request, the card, the approval, the edit and the run. Against the stand-in
(fake_azure.py); no test reaches Microsoft. The guard's own table is test_azure_logs_guard.py. No database is needed.

Response shapes: written from documented output, not run against a tenant. The first body below is copied from
https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/response-format (the `AzureActivity | summarize count()
by Category` example) and the error bodies from .../logs/api/timeouts ("Common API errors")."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from helpers import api_policy, dotenv, rbac, secrets, tool_permissions, user_scope
from plugins._audit.helpers import audit
from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPROVED, AWAITING_APPROVAL, CLOSED, FAILED
from plugins._ea_azure.api.azure_config import AzureConfig
from plugins._ea_azure.api.azure_read_approve import AzureReadApprove
from plugins._ea_azure.api.azure_read_edit import AzureReadEdit
from plugins._ea_azure.extensions.python.tool_execute_before._95_azure_read_gate import AzureReadGate  # noqa: F401
from plugins._ea_azure.helpers import client, config, credential, live, logs
from plugins._ea_azure.tests.fake_azure import LOG_CLIENT_ID, LOGS_SCOPE, TENANT_ID, FakeAzure, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install
from plugins._ea_azure.tools import azure_read
from plugins._ea_interaction.helpers import cards

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

WS = "5d1a7c30-6c1e-4b0e-8f4a-2b9d3e5f7a01"
OTHER_WS = "9e8d7c6b-5a49-4382-9170-0f1e2d3c4b5a"
WORKSPACES = [{"name": "prod", "id": WS}]

# Microsoft's documented example (response-format page).
DOC_BODY = {"tables": [{"name": "PrimaryResult", "columns": [{"name": "Category", "type": "string"}, {"name": "count_", "type": "long"}],
                        "rows": [["Administrative", 20839], ["Recommendation", 122], ["Alert", 64], ["ServiceHealth", 11]]}]}
SYNTAX_ERROR = {"error": {"message": "The request had some invalid properties", "code": "BadArgumentError",
                          "innererror": {"code": "SyntaxError", "message": "Syntax Error"}}}  # timeouts page


@pytest.fixture(scope="module")
def blob():
    return credential._new_blob("rezztor-netops-logs")  # one 3072-bit key for the module, not one per test


@pytest.fixture
def env(monkeypatch, tmp_path, blob):
    store = install(monkeypatch, tmp_path)
    store[credential.KEY_AZURE_LOGS_CERT] = blob
    config.save({"tenant_id": TENANT_ID, "client_id": "b93d5f08-0000-4000-8000-a4c27e118c53",
                 "logs": {"client_id": LOG_CLIENT_ID, "workspaces": WORKSPACES}})
    monkeypatch.setattr(logs, "_sleep", lambda s: None)
    monkeypatch.setattr(logs, "_busy", set())
    logs.reset()
    return store


@pytest.fixture
def azure(env, monkeypatch):
    with FakeAzure(hand_tenant()) as fake:
        fake.register_log_certificate(credential.public_logs_certificate())
        fake.log_answers[WS] = (200, DOC_BODY, {})
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        yield fake
    logs.reset()


def sent(fake):
    return [r for r in fake.received if not r["path"].endswith("/oauth2/v2.0/token")]


def body_of(entry):
    return json.loads(entry["raw"])


# --- settings --------------------------------------------------------------------------------------------------------

def test_the_settings_have_safe_defaults_and_a_damaged_one_only_narrows():
    clean = logs.clean({})
    assert clean == {"client_id": "", "workspaces": [], "tables": list(logs.DEFAULT_TABLES), "max_timespan": "P7D"}
    for table in ("SigninLogs", "AuditLogs", "SecurityEvent", "Event", "AzureActivity"):
        assert table not in clean["tables"]
    odd = logs.clean({"client_id": "nope", "workspaces": [{"name": "a b", "id": WS}, {"name": "ok", "id": "x"}, "text"],
                      "tables": ["Good", "bad name", "x;y"], "max_timespan": "P99D"})
    assert odd == {"client_id": "", "workspaces": [], "tables": ["Good"], "max_timespan": "P7D"}
    assert logs.clean({"tables": []})["tables"] == []  # an empty list means nothing can be queried


@pytest.mark.parametrize("raw", [
    {"client_id": "not-a-guid"}, {"workspaces": [{"name": "a b", "id": WS}]}, {"workspaces": [{"name": "w", "id": "short"}]},
    {"workspaces": [{"name": "w", "id": WS}, {"name": "W", "id": OTHER_WS}]}, {"tables": ["bad name"]},
    {"max_timespan": "P31D"}, {"max_timespan": "PT5M"}, {"max_timespan": "soon"},
])
def test_saving_a_wrong_setting_is_refused_with_a_sentence(raw):
    with pytest.raises(ValueError):
        logs.clean(raw, strict=True)


def test_a_good_setting_is_normalised_on_save():
    got = logs.clean({"client_id": LOG_CLIENT_ID.upper(), "workspaces": [{"name": "prod", "id": WS.upper()}],
                      "tables": ["Heartbeat", "Heartbeat", " Perf "], "max_timespan": "p1d"}, strict=True)
    assert got == {"client_id": LOG_CLIENT_ID, "workspaces": WORKSPACES, "tables": ["Heartbeat", "Perf"], "max_timespan": "P1D"}


# --- the second certificate --------------------------------------------------------------------------------------------

def test_the_log_certificate_is_separate_from_the_read_credential(env):
    credential.set_secret("read-secret", "2027-01-01")
    before = dict(env)
    status = credential.generate_logs_certificate()
    assert status["set"] and status["fingerprint"] and "kind" not in status
    assert env[dotenv.KEY_AZURE_CLIENT_SECRET] == before[dotenv.KEY_AZURE_CLIENT_SECRET] == "read-secret"
    assert credential.status()["kind"] == "secret"  # the read credential did not change
    pem = credential.public_logs_certificate()
    assert pem.startswith(b"-----BEGIN CERTIFICATE-----") and b"PRIVATE" not in pem
    assert "PRIVATE KEY" in credential._logs_material() and credential._material() == ("secret", "read-secret")
    credential.clear_logs()
    assert credential.logs_status() == {"set": False, "fingerprint": "", "expires_on": ""} and credential._logs_material() == ""
    assert credential.status()["kind"] == "secret"
    with pytest.raises(ValueError):
        credential.public_logs_certificate()


def test_the_log_key_name_is_not_an_agent_visible_secret_name():
    assert credential.KEY_AZURE_LOGS_CERT == "REZZTOR_AZURE_LOGS_CERT"
    assert not credential.KEY_AZURE_LOGS_CERT.startswith("API_KEY")
    assert credential.KEY_AZURE_LOGS_CERT not in set(secrets._RUNTIME_CREDENTIAL_KEYS)


# --- the call ----------------------------------------------------------------------------------------------------------

def test_a_query_signs_in_as_the_log_app_and_sends_one_post_to_the_pinned_workspace(azure):
    out = logs.query("prod", "Heartbeat | take 5", "PT6H", "chat1")
    (one,) = sent(azure)
    assert (one["method"], one["path"]) == ("POST", f"/v1/workspaces/{WS}/query") and one["prefer"] == "wait=60"
    assert body_of(one) == {"query": "Heartbeat | take 5" + logs.TAKE, "timespan": "PT6H"}  # nothing else: no `workspaces`
    form = next(r for r in azure.received if r["path"].endswith("/oauth2/v2.0/token"))["form"]
    assert form["client_id"] == LOG_CLIENT_ID and form["scope"] == LOGS_SCOPE and "client_secret" not in form
    assert azure.log_tokens and not azure.tokens  # the read app's audience was never asked for
    assert out["result"] == {"columns": ["Category", "count_"], "rows": DOC_BODY["tables"][0]["rows"]}
    assert out["rows"] == 4 and out["truncated"] is False and out["status"] == 200


def test_the_token_is_reused_and_a_401_signs_in_again_once(azure):
    logs.query("prod", "Heartbeat", "PT1H")
    logs.query("prod", "Heartbeat", "PT1H")
    assert len([r for r in azure.received if r["path"].endswith("/oauth2/v2.0/token")]) == 1
    azure.log_answers[WS] = [(401, {"error": {"code": "AuthenticationFailed"}}, {}), (200, DOC_BODY, {})]
    assert logs.query("prod", "Heartbeat", "PT1H")["rows"] == 4
    assert len([r for r in azure.received if r["path"].endswith("/oauth2/v2.0/token")]) == 2
    azure.log_answers[WS] = (401, {"error": {}}, {})
    with pytest.raises(live.ReadUnavailable, match="HTTP 401"):
        logs.query("prod", "Heartbeat", "PT1H")


def test_identity_columns_are_dropped_urls_lose_their_query_string_and_every_cell_is_masked_and_cut(azure):
    names = ["TimeGenerated", "Claims", "HTTPRequest", "Authorization", "CsUsername", "CsUriQuery", "UserAgent", "CallerIpAddress",
             "TargetUrl", "CsUriStem", "Note"]
    row = ["2026-10-03T10:00:00Z", '{"upn":"a@b.c"}', "GET /x", "Bearer abc", "alice", "token=1", "curl/8", "203.0.113.9",
           "https://example.com/p?token=SECRET123&q=1", "/search?q=private", "password=hunter2 and " + "x" * 600]
    azure.log_answers[WS] = (200, {"tables": [{"name": "PrimaryResult", "columns": [{"name": n, "type": "string"} for n in names], "rows": [row]}]}, {})
    out = logs.query("prod", "Heartbeat", "PT1H")
    assert out["result"]["columns"] == ["TimeGenerated", "TargetUrl", "CsUriStem", "Note"]
    cells = dict(zip(out["result"]["columns"], out["result"]["rows"][0]))
    assert cells["TargetUrl"] == "https://example.com/p" and cells["CsUriStem"] == "/search"
    assert cells["Note"] == "[masked]"
    blob = json.dumps(out)
    for leaked in ("alice", "203.0.113.9", "curl/8", "SECRET123", "private", "hunter2", "a@b.c", "Bearer"):
        assert leaked not in blob


def test_a_column_azure_renamed_is_still_an_identity_column_and_a_url_is_cut_under_any_name(azure):
    """A join gives the right side's copy of a column the same name with a 1 after it, a union of two types adds
    `_string`, an aggregate puts its own name in front: `CallerIpAddress1` came back whole. And `project u = TargetUrl`
    gave a column the URL rule did not know, so the query string (a SAS signature, a search) stayed on it."""
    names = ["TimeGenerated", "CallerIpAddress1", "UserAgent_string", "any_Claims", "u", "TargetUrl1"]
    row = ["2026-10-03T10:00:00Z", "203.0.113.9", "curl/8", '{"upn":"a@b.c"}', "https://example.com/p?sig=SECRET123",
           "HTTPS://example.com/q?search=private"]
    azure.log_answers[WS] = (200, {"tables": [{"name": "PrimaryResult", "columns": [{"name": n} for n in names], "rows": [row]}]}, {})
    out = logs.query("prod", "Heartbeat", "PT1H")
    assert out["result"]["columns"] == ["TimeGenerated", "u", "TargetUrl1"]
    assert out["result"]["rows"][0][1:] == ["https://example.com/p", "HTTPS://example.com/q"]


def test_a_long_cell_is_cut_and_a_dynamic_cell_is_flattened(azure):
    azure.log_answers[WS] = (200, {"tables": [{"name": "PrimaryResult", "columns": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
                                               "rows": [["y" * 900, {"k": [1, 2]}, None]]}]}, {})
    row = logs.query("prod", "Heartbeat", "PT1H")["result"]["rows"][0]
    assert len(row[0]) == 512 and row[1] == '{"k":[1,2]}' and row[2] is None


def test_rows_stop_at_200_and_at_64_kb_and_say_so(azure):
    columns = [{"name": "A", "type": "string"}]
    azure.log_answers[WS] = (200, {"tables": [{"name": "PrimaryResult", "columns": columns, "rows": [[str(i)] for i in range(201)]}]}, {})
    out = logs.query("prod", "Heartbeat", "PT1H")
    assert out["rows"] == 200 and out["truncated"] is True and out["result"]["rows"][-1] == ["199"]
    wide = [["w" * 500 + str(i)] for i in range(200)]
    azure.log_answers[WS] = (200, {"tables": [{"name": "PrimaryResult", "columns": columns, "rows": wide}]}, {})
    out = logs.query("prod", "Heartbeat", "PT1H")
    assert out["truncated"] is True and 0 < out["rows"] < 200 and len(json.dumps(out["result"])) <= logs.MAX_RESULT


def test_a_partial_error_and_no_data_are_reported(azure):
    azure.log_answers[WS] = (200, {**DOC_BODY, "error": {"code": "PartialError", "message": "x", "details": []}}, {})
    out = logs.query("prod", "Heartbeat", "PT1H")
    assert out["partial"] is True and out["truncated"] is True and out["rows"] == 4
    azure.log_answers[WS] = (204, "", {})
    assert logs.query("prod", "Heartbeat", "PT1H")["rows"] == 0


@pytest.mark.parametrize("status,body,text", [
    (403, {"error": {"code": "InsufficientAccessError", "message": "SECRET-ERROR-TEXT"}}, "lacks the Log Analytics Reader role"),
    (404, {"error": {"code": "PathNotFoundError"}}, "no such workspace"),
    (504, {"error": {}}, "longer than 60 seconds"),
    (503, {"error": {}}, "had a problem answering"),
    (418, {"error": {}}, "could not answer the query (HTTP 418)"),
])
def test_other_refusals_are_the_plugins_own_sentences(azure, status, body, text):
    azure.log_answers[WS] = (status, body, {})
    with pytest.raises(live.ReadUnavailable, match=re.escape(text)) as error:
        logs.query("prod", "Heartbeat", "PT1H")
    assert error.value.status == status and "SECRET-ERROR-TEXT" not in str(error.value)


def test_a_syntax_error_says_what_azure_said_masked_and_short(azure):
    azure.log_answers[WS] = (400, SYNTAX_ERROR, {})
    with pytest.raises(live.ReadUnavailable, match="HTTP 400.*Syntax Error"):
        logs.query("prod", "Heartbeat | whare x", "PT1H")
    azure.log_answers[WS] = (400, {"error": {"innererror": {"message": "password=hunter2 " + "z" * 500}}}, {})
    with pytest.raises(live.ReadUnavailable) as error:
        logs.query("prod", "Heartbeat", "PT1H")
    assert "hunter2" not in str(error.value) and len(str(error.value)) < 400


def test_a_429_is_waited_out_once_then_refused(azure, monkeypatch):
    waits = []
    monkeypatch.setattr(logs, "_sleep", waits.append)
    azure.log_answers[WS] = [(429, {}, {"Retry-After": "7"}), (200, DOC_BODY, {})]
    assert logs.query("prod", "Heartbeat", "PT1H")["rows"] == 4 and waits == [7]
    azure.log_answers[WS] = (429, {}, {"Retry-After": "900"})
    with pytest.raises(live.ReadUnavailable, match="HTTP 429"):
        logs.query("prod", "Heartbeat", "PT1H")
    assert waits[-1] == logs.MAX_PAUSE


def test_a_redirect_is_never_followed_and_odd_answers_are_refused(azure):
    azure.log_answers[WS] = (302, {}, {"Location": "https://evil.example.com/steal"})
    with pytest.raises(live.ReadUnavailable, match="redirect"):
        logs.query("prod", "Heartbeat", "PT1H")
    assert [r for r in sent(azure) if "evil" in r["path"]] == []
    azure.log_answers[WS] = (200, "not json{", {})
    with pytest.raises(live.ReadUnavailable, match="cannot read"):
        logs.query("prod", "Heartbeat", "PT1H")
    azure.log_answers[WS] = (200, {"tables": []}, {})
    with pytest.raises(live.ReadUnavailable, match="cannot read"):
        logs.query("prod", "Heartbeat", "PT1H")


def test_a_sign_in_failure_is_a_sentence_without_the_certificate(azure, env):
    azure.sign_in_error = "certificate"
    with pytest.raises(live.ReadUnavailable) as error:
        logs.query("prod", "Heartbeat", "PT1H")
    assert "certificate" in str(error.value).lower() and "PRIVATE" not in str(error.value) and sent(azure) == []


def test_nothing_is_sent_for_a_query_the_guard_a_workspace_or_a_time_range_refuses(azure):
    for args, match in (
        (("prod", "SigninLogs | take 1", "PT1H"), "SigninLogs"),
        (("prod", ".show tables", "PT1H"), "dot"),
        (("other", "Heartbeat", "PT1H"), "not a workspace"),
        (("prod", "Heartbeat", "P8D"), "between PT1M and P7D"),
        (("prod", "Heartbeat", "PT0M"), "between PT1M"),
        (("prod", "Heartbeat", "soon"), "not a duration"),
    ):
        with pytest.raises(live.Refused, match=match):
            logs.query(*args)
    assert sent(azure) == [] and azure.received == []


def test_the_text_is_guarded_again_at_run_even_if_a_table_was_removed_since_approval(azure):
    config.save({"logs": {**logs.settings(), "tables": ["Perf"]}})
    with pytest.raises(live.Refused, match="Heartbeat"):
        logs.query("prod", "Heartbeat", "PT1H")
    assert sent(azure) == []


def test_one_query_per_chat_at_a_time_and_the_slot_is_released(azure, monkeypatch):
    seen = []

    def slow(url, body):
        if seen:  # the nested call from another chat: answered normally
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: DOC_BODY)
        seen.append(set(logs._busy))
        with pytest.raises(live.Refused, match="already running"):
            logs.query("prod", "Heartbeat", "PT1H", "chat1")
        assert logs.query("prod", "Heartbeat", "PT1H", "chat2")["rows"] == 4  # another chat is not blocked
        raise live.ReadUnavailable("boom")

    monkeypatch.setattr(logs, "_send", slow)
    with pytest.raises(live.ReadUnavailable, match="boom"):
        logs.query("prod", "Heartbeat", "PT1H", "chat1")
    assert seen[0] == {"chat1"} and logs._busy == set()


def test_default_workspace_is_the_only_one_and_several_need_a_name(env):
    assert logs.workspace_for("")["id"] == WS and logs.workspace_for("PROD")["id"] == WS
    config.save({"logs": {**logs.settings(), "workspaces": WORKSPACES + [{"name": "dev", "id": OTHER_WS}]}})
    with pytest.raises(live.Refused, match="Say which workspace"):
        logs.workspace_for("")
    assert logs.workspace_for("dev")["id"] == OTHER_WS


def test_the_source_has_one_verb_one_host_and_no_redirects():
    source = Path(logs.__file__).read_text()
    assert source.count("requests.post(") == 1 and "requests.get(" not in source and "requests.request(" not in source
    assert "allow_redirects=False" in source and "allow_redirects=True" not in source
    code = source.split("HOST = ", 1)[1]  # after the module docstring
    assert logs.HOST == "https://api.loganalytics.azure.com" and re.findall(r"https?://[^\s\"']+", code) == [logs.HOST, logs.SCOPE]
    assert not re.search(r"\b(DELETE|PUT|PATCH)\b", source) and '"workspaces"' not in source.split("def query")[1]


# --- the named log reads -----------------------------------------------------------------------------------------------

def test_the_table_has_named_log_reads_that_pass_the_guard_and_never_clash_with_arm_reads():
    entries = logs.load()
    assert {"fw_network_denied", "fw_application_denied", "heartbeat_last_seen"} <= set(entries)
    assert not set(entries) & set(live.load())  # `live.load()` skips `kind: kql` rows
    for entry in entries.values():
        final, refusal = logs.guard(logs.render(entry, {}), entry.tables)
        assert refusal == "" and final.endswith(logs.TAKE)
        assert set(entry.tables) <= set(logs.DEFAULT_TABLES)
    assert "fw_network_denied" in logs.catalogue() and logs.known("kql") and not logs.known("vm_metrics")


def test_the_rendered_text_holds_typed_values_as_string_literals_only(env):
    made = logs.prepare("fw_network_denied", "", "prod", {"ip": "10.0.0.4", "port": "443", "duration": "pt6h"})
    text = "\n".join(made["commands"])
    assert 'SourceIp == "10.0.0.4"' in text and 'tostring(DestinationPort) == "443"' in text
    assert made["variables"] == {"ip": "10.0.0.4", "port": "443", "duration": "PT6H", "workspace": "prod", "timespan": "PT6H"}
    assert made["limit"] == "prod" and made["entry"].name == "fw_network_denied"
    nothing = "\n".join(logs.prepare("fw_network_denied", "", "prod", {})["commands"])
    assert 'isempty("")' in nothing and "10.0" not in nothing
    for bad in ({"ip": '1.1.1.1" or 1==1 //'}, {"port": "443; .drop table x"}, {"duration": "P30D"}, {"ip": "10.0.0.4", "x": "1"}):
        with pytest.raises(live.Refused):
            logs.prepare("fw_network_denied", "", "prod", bad)
    assert logs.literal('a"b\\c') == '"a\\"b\\\\c"'


def test_a_loader_refuses_a_log_read_that_does_not_pass_the_guard_or_has_stray_holes():
    base = {"name": "x_read", "kind": "kql", "summary": "s", "tables": ["Heartbeat"], "text": "Heartbeat | take 1"}
    assert logs.parse(yaml.safe_dump([base]))["x_read"]
    for change in ({"text": "SigninLogs | take 1"}, {"text": "Heartbeat | where x == {ip}"}, {"tables": []}, {"summary": ""},
                   {"args": {"ip": {"type": "ip"}}}, {"args": {"a": {"type": "string"}}}, {"name": "Bad Name"},
                   {"text": "externaldata (x:string) ['http://e/x'] | take 1"}):
        with pytest.raises(live.ReadsInvalid):
            logs.parse(yaml.safe_dump([{**base, **change}]))
    with pytest.raises(live.ReadsInvalid):
        logs.parse(yaml.safe_dump([base, base]))


def test_prepare_needs_the_log_app_a_workspace_and_one_of_a_name_or_kql(env, monkeypatch):
    with pytest.raises(live.Refused, match="not both"):
        logs.prepare("heartbeat_last_seen", "Heartbeat", "prod", {})
    with pytest.raises(live.Refused, match="`kql`, `workspace` and `timespan`"):
        logs.prepare("", "Heartbeat", "prod", {"ip": "10.0.0.1"})
    credential.clear_logs()
    with pytest.raises(live.Refused, match="not set up"):
        logs.prepare("", "Heartbeat", "prod", {})


# --- azure_read: request, card, approval, edit, run ---------------------------------------------------------------------

@pytest.fixture
def world(azure, monkeypatch):
    monkeypatch.setattr(tool_permissions, "role_for_context", lambda ctx: "power")
    events, shown = [], []
    monkeypatch.setattr(azure_read.audit, "emit", lambda event, **kw: event.startswith("azure.") and events.append((event, kw)))
    monkeypatch.setattr(azure_read.AzureRead, "_show_card", lambda self, record, entry, editable=False: shown.append((record.id, editable)))
    azure.events, azure.cards = events, shown
    with user_scope.user_scope("asha"):
        yield azure


def ask(ctx="chat1", **args):
    agent = SimpleNamespace(context=SimpleNamespace(id=ctx), number=0)
    tool = azure_read.AzureRead(agent=agent, name="azure_read", method=None, args=args, message="", loop_data=None)
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def only():
    (record,) = [r for r in change_store.list_records() if r.kind == azure_read.KIND]
    return record


def approve(record, by="asha"):
    with user_scope.user_scope(by):
        return asyncio.run(AzureReadApprove(None, None).process({"change_id": record.id, "decision": "approve", "expected_hash": record.current_hash}, None))


def edit(record, **body):
    return asyncio.run(AzureReadEdit(None, None).process({"change_id": record.id, "expected_hash": record.current_hash, **body}, None))


def test_a_free_form_request_records_the_checked_text_and_shows_an_editable_card(world):
    message = ask(kql="Heartbeat | where Computer startswith 'vm-' // mine", workspace="prod", timespan="PT30M")
    record = only()
    assert (record.kind, record.status, record.playbook_name, record.limit) == ("azure_read", AWAITING_APPROVAL, "kql", "prod")
    assert record.variables == {"workspace": "prod", "timespan": "PT30M"} and record.commit == live.commit()
    assert record.commands == ["Heartbeat | where Computer startswith 'vm-'", "| take 201"]  # no comment, cap added
    assert world.cards == [(record.id, True)] and sent(world) == [] and "Nothing has been sent to Azure" in message
    assert [e for e, _ in world.events] == ["azure.read.requested"] and 890 < record.expires_at - time.time() <= 900
    card = azure_read.read_card(record, logs.FREE_ENTRY, editable=True)
    flat = json.dumps(card)
    assert card["edit_endpoint"] == "plugins/_ea_azure/azure_read_edit" and "plugins/_ea_azure/azure_read_approve" in flat
    assert "Log Analytics Reader" in flat and "prod" in flat and "PT30M" in flat and record.current_hash in flat
    assert "plugins/_ea_azure/azure_read_edit" in cards.EDIT_ENDPOINTS


def test_what_cannot_be_asked_gets_a_sentence_and_no_record(world):
    assert "not both" in ask(read="heartbeat_last_seen", kql="Heartbeat", workspace="prod")
    assert "SigninLogs" in ask(kql="SigninLogs | take 1", workspace="prod")
    assert "Say which workspace" not in ask(kql="SigninLogs", workspace="prod")  # one workspace: the name may be left out
    assert "not a workspace" in ask(kql="Heartbeat", workspace="nope")
    assert "between PT1M" in ask(kql="Heartbeat", workspace="prod", timespan="P8D")
    credential.set_secret("read-secret", "")  # the Resource Manager side needs the read app; the unknown-name answer lists both kinds
    assert "kql" in ask(read="nope", resource="x") and "fw_network_denied" in ask(read="nope", resource="x")
    assert [r for r in change_store.list_records() if r.kind == azure_read.KIND] == [] and world.cards == [] and sent(world) == []


def test_not_set_up_is_said_plainly(world):
    credential.clear_logs()
    assert "not set up" in ask(kql="Heartbeat", workspace="prod")


def test_free_form_text_always_asks_even_when_the_setting_would_not(world, monkeypatch):
    monkeypatch.setattr(live, "read_approval", lambda: "log_only")
    ask(kql="Heartbeat", workspace="prod")
    record = only()
    assert record.status == AWAITING_APPROVAL and record.approval_basis == "" and world.cards


def test_a_named_log_read_can_be_covered_by_the_setting_for_an_engineer_who_could_approve(world, monkeypatch):
    monkeypatch.setattr(live, "read_approval", lambda: "log_only")
    message = ask(read="heartbeat_last_seen", workspace="prod", duration="P1D")
    record = only()
    assert record.status == APPROVED and record.approval_basis == "standing:log_only" and world.cards == [] and "standing read setting" in message
    assert [e for e, _ in world.events] == ["azure.read.requested", "azure.read.auto_approved"]
    assert "Last seen" not in message and "PrimaryResult" not in message
    monkeypatch.setattr(tool_permissions, "role_for_context", lambda ctx: "user")
    other = ask(read="heartbeat_last_seen", workspace="prod")
    assert "waiting for the engineer's approval" in other  # a requester who may not approve still gets a card


def test_run_refuses_until_approved_then_runs_once_and_keeps_no_answer(world):
    ask(kql="AZFWNetworkRule | where Action == 'Deny' | summarize count() by Rule", workspace="prod", timespan="PT2H")
    record = only()
    assert "not approved yet" in ask(action="run", change_id=record.id) and sent(world) == []
    assert approve(record)["ok"]
    out = ask(action="run", change_id=record.id)
    assert "Administrative" in out and "not stored" in out and "not a standing fact" in out
    (one,) = sent(world)
    assert body_of(one) == {"query": "\n".join(record.commands), "timespan": "PT2H"} and one["path"] == f"/v1/workspaces/{WS}/query"
    done = change_store.load(record.id)
    assert done.status == CLOSED and done.exit_code == 200 and "Administrative" not in json.dumps(done.__dict__, default=str)
    run = [kw for e, kw in world.events if e == "azure.logs.query"]
    assert run == [{"change_id": record.id, "read": "kql", "resource": "prod", "requested_by": "asha", "approved_by": "asha",
                    "workspace": "prod", "timespan": "PT2H", "sha256": hashlib.sha256("\n".join(record.commands).encode()).hexdigest(), "rows": 4}]
    assert "not approved" in ask(action="run", change_id=record.id) and len(sent(world)) == 1  # once only


def test_a_failed_query_closes_the_record_failed_with_azures_status(world):
    world.log_answers[WS] = (403, {}, {})
    ask(read="heartbeat_last_seen", workspace="prod")
    record = only()
    approve(record)
    assert "lacks the Log Analytics Reader role" in ask(action="run", change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == FAILED and done.exit_code == 403
    assert not [e for e, _ in world.events if e == "azure.logs.query"]


def test_a_changed_table_or_a_changed_record_or_someone_else_cannot_run_it(world, monkeypatch):
    ask(kql="Heartbeat", workspace="prod")
    record = only()
    approve(record)
    approved = change_store.load(record.id)
    assert azure_read.run_refusal(approved, "asha") is None
    assert "not made by the current user" in azure_read.run_refusal(approved, "mallory")
    approved.commands = ["Heartbeat | take 5000", "| take 201"]  # edited after approval
    change_store.save(approved)
    assert azure_read.run_refusal(change_store.load(record.id), "asha")  # the approved hash no longer matches
    assert "no longer" in ask(action="run", change_id=record.id) or "changed" in ask(action="run", change_id=record.id)
    assert sent(world) == []


def test_the_run_gate_refuses_an_unapproved_or_foreign_log_request_and_holds_the_table_version(world, monkeypatch):
    ask(read="heartbeat_last_seen", workspace="prod")
    record = only()
    assert azure_read.run_refusal(record, "asha")  # not approved
    approve(record)
    record = change_store.load(record.id)
    assert azure_read.run_refusal(record, "asha") is None
    monkeypatch.setattr(live, "commit", lambda: "another")
    assert "changed since this was approved" in azure_read.run_refusal(record, "asha")


def test_the_requester_edits_the_text_and_the_new_text_is_what_is_approved_and_run(world):
    ask(kql="Heartbeat | take 5", workspace="prod")
    record = only()
    old_hash = record.current_hash
    out = edit(record, commands=["Heartbeat", "| where Computer == 'vm-1'", "| take 10"])
    assert out["ok"] and out["card"]["edit_endpoint"] == "plugins/_ea_azure/azure_read_edit"
    edited = change_store.load(record.id)
    assert edited.commands == ["Heartbeat", "| where Computer == 'vm-1'", "| take 10", "| take 201"]
    assert edited.current_hash != old_hash and edited.status == AWAITING_APPROVAL
    assert [kw for e, kw in world.events if e == "azure.read.edited"][0]["hash_before"] == old_hash
    stale = asyncio.run(AzureReadApprove(None, None).process({"change_id": record.id, "decision": "approve", "expected_hash": old_hash}, None))
    assert not stale["ok"] and "no longer what would be sent" in stale["error"]
    assert approve(edited)["ok"]
    ask(action="run", change_id=record.id)
    assert body_of(sent(world)[0])["query"] == "\n".join(edited.commands)


@pytest.mark.parametrize("body,text", [
    ({"commands": [".drop table Heartbeat"]}, "dot"),
    ({"commands": ["SigninLogs"]}, "SigninLogs"),
    ({"commands": []}, "nothing to run"),
    ({"commands": ["Heartbeat"], "expected_hash": "stale"}, "no longer what would run"),
])
def test_an_edit_the_guard_or_the_hash_refuses_changes_nothing(world, body, text):
    ask(kql="Heartbeat | take 5", workspace="prod")
    record = only()
    out = edit(record, **body)
    assert not out["ok"] and text in out["error"] and change_store.load(record.id).commands == record.commands


def test_only_the_requester_can_edit_and_only_a_free_form_query_awaiting_approval(world, monkeypatch):
    ask(kql="Heartbeat | take 5", workspace="prod")
    free = only()
    with user_scope.user_scope("mallory"):
        assert edit(free, commands=["Heartbeat"])["ok"] is False  # someone else's change id is "not found"
    approve(free)
    assert "awaiting approval" in edit(change_store.load(free.id), commands=["Heartbeat"])["error"]
    ask(read="heartbeat_last_seen", workspace="prod")
    named = [r for r in change_store.list_records() if r.playbook_name == "heartbeat_last_seen"][0]
    assert "Only a free-form" in edit(named, commands=["Heartbeat"])["error"]


def test_an_expired_request_cannot_be_edited(world):
    ask(kql="Heartbeat | take 5", workspace="prod")
    record = only()
    record.expires_at = time.time() - 1
    change_store.save(record)
    assert "expired" in edit(record, commands=["Heartbeat"])["error"]


def test_the_edit_endpoint_is_in_the_policy_table_and_the_handler_does_not_widen_it():
    assert api_policy.required_permission_for("plugins/_ea_azure/azure_read_edit") == rbac.PERM_TOOL_NETWORK
    assert AzureReadEdit.required_permission() is None  # the policy table decides; the handler never overrides it


# --- the settings endpoint -----------------------------------------------------------------------------------------------

def call_config(**payload):
    return asyncio.run(AzureConfig.__new__(AzureConfig).process(payload, None))


@pytest.fixture
def api(env, monkeypatch):
    events = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: events.append((event, fields)))
    return events


def test_the_log_settings_and_the_read_approval_save_and_come_back_normalised(api):
    out = call_config(action="save", config={"read_approval": "once_per_chat", "logs": {
        "client_id": LOG_CLIENT_ID, "workspaces": WORKSPACES, "tables": ["Heartbeat"], "max_timespan": "P1D"}})
    assert out["ok"] and out["config"]["read_approval"] == "once_per_chat"
    assert out["config"]["logs"] == {"client_id": LOG_CLIENT_ID, "workspaces": WORKSPACES, "tables": ["Heartbeat"], "max_timespan": "P1D"}
    assert live.read_approval() == "once_per_chat" and logs.settings()["tables"] == ["Heartbeat"]
    assert out["logs_credential"]["set"] is True and "PRIVATE" not in json.dumps(out)
    assert set(dict(api)["azure.settings_saved"]["changed"]) == {"read_approval", "logs"}


def test_a_wrong_log_setting_or_read_approval_is_refused_and_nothing_is_saved(api):
    before = config.get_config()
    for bad in ({"read_approval": "whenever"}, {"logs": {"client_id": "nope"}}, {"logs": {"tables": ["a b"]}}, {"logs": {"max_timespan": "P90D"}}):
        out = call_config(action="save", config=bad)
        assert out["ok"] is False and out["error"]
    assert config.get_config() == before and not [e for e, _ in api if e == "azure.settings_saved"]


def test_the_log_certificate_is_made_shown_publicly_and_cleared_without_the_key_leaving(api, env, blob, monkeypatch):
    monkeypatch.setattr(credential, "_new_blob", lambda prefix: blob)
    credential.clear_logs()
    out = call_config(action="generate_logs_certificate")
    assert out["ok"] and out["logs_credential"]["set"] and out["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE" not in json.dumps(out) and call_config(action="public_logs_certificate")["public_certificate"] == out["public_certificate"]
    assert [kw for e, kw in api if e == "azure.logs_credential_set"] == [{"fingerprint": out["logs_credential"]["fingerprint"],
                                                                         "expires_on": out["logs_credential"]["expires_on"]}]
    cleared = call_config(action="clear_logs_credential")
    assert cleared["logs_credential"]["set"] is False and ("azure.logs_credential_cleared", {}) in api
    assert call_config(action="public_logs_certificate")["ok"] is False
    assert "PRIVATE" not in json.dumps(call_config(action="get"))
