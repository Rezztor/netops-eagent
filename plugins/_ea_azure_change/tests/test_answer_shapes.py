"""The answers the Azure change screens read, pinned from both sides. `frontend/src/api/azureChange.ts` was written against
assumed answers; this test reads the field names out of that file and holds them to what the endpoints really return (the
executor's own `rails.defaults()` and `keys.status()` stand in for its admin answers), so a rename on either side fails here
and not in someone's browser. Reuses the flow test's stand-in executor and helpers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from cloud_executor import keys as executor_keys, rails as executor_rails
from plugins._ea_ansible.helpers import change_store
from plugins._ea_azure_change.api import azure_change_config
from plugins._ea_azure_change.api.azure_changes import AzureChanges
from plugins._ea_azure_change.api.azure_edit import AzureEdit
from plugins._ea_azure_change.helpers import executor_client, records
from plugins._ea_azure_change.tests.fake_executor import DIGEST_B, plan_answer
from plugins._ea_azure_change.tests.test_azure_change_flow import approved, decide, download, draft, world  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

TS = (Path(__file__).resolve().parents[3] / "frontend/src/api/azureChange.ts").read_text()


def ts_fields(interface: str) -> set[str]:
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", TS, re.S).group(1)
    return set(re.findall(r"^\s{2}(\w+)\??:", body, re.M))


def config(**body):
    return asyncio.run(azure_change_config.AzureChangeConfig(None, None).process(body, None))


def test_the_change_list_rows_carry_every_field_the_changes_tab_reads(world, monkeypatch):  # noqa: F811
    approved(world, "CR-11111")
    answer = asyncio.run(AzureChanges(None, None).process({}, None))
    (row,) = answer["changes"]
    assert answer["enabled"] is True
    monkeypatch.setattr(records, "enabled", lambda: False)  # an off plugin still answers; the screen reads `enabled` to draw nothing
    assert asyncio.run(AzureChanges(None, None).process({}, None))["enabled"] is False
    assert ts_fields("AzureChangeSummary") <= set(row)
    assert row["reference"] == "CR-11111" and isinstance(row["created_at"], float)
    for doc in row["documents"]:
        assert ts_fields("AzureChangeDocument") <= set(doc)
        assert isinstance(doc["downloaded"], bool)  # a flag, not a time: the screen reads `downloaded`


def test_the_settings_answers_have_the_names_the_settings_screen_reads(monkeypatch, tmp_path):
    monkeypatch.setenv(executor_keys.ENV_STATE_DIR, str(tmp_path))
    rails = executor_rails.defaults(["azurerm_virtual_network"])
    assert ts_fields("Guardrails") == set(rails) == set(azure_change_config.RAILS)
    assert set(rails["resource_groups"]) == {"existing", "new_prefix"}
    status = executor_keys.status()
    assert set(status) == {"planner", "applier", "passphrase_set"} == ts_fields("KeysState")
    assert set(status["planner"]) == ts_fields("KeyState")

    answers = {("GET", "/v1/admin/guardrails"): rails, ("PUT", "/v1/admin/guardrails"): rails, ("GET", "/v1/admin/keys"): status,
               ("POST", "/v1/admin/keys/planner"): {"ok": True, "public_certificate": "PEM", "thumbprint": "AB", "expires_on": "2027-10-03"},
               ("POST", "/v1/admin/test"): {"planner": {"ok": True}, "applier": {"ok": True}, "role_ok": False, "reason": "holds Contributor"}}
    monkeypatch.setattr(executor_client, "admin", lambda agent, method, path, body=None: answers[(method, path)])
    monkeypatch.setattr(azure_change_config.AzureChangeConfig, "_save_local", staticmethod(lambda rails: None))
    assert config(action="get")["guardrails"] == rails
    assert config(action="save_guardrails", guardrails=rails)["guardrails"] == rails
    keys = config(action="keys")  # the screen reads these at the top level (or under `keys`)
    assert {"planner", "applier", "passphrase_set"} <= set(keys) and keys["passphrase_set"] is False
    # the screen sends `role`, not `which`
    assert config(action="make_key", role="planner", tenant_id="t", client_id="c")["public_certificate"] == "PEM"
    assert not config(action="make_key", which="planner", tenant_id="t", client_id="c")["ok"]
    assert (lambda r: (r["ok"], r["role_ok"], r["reason"]))(config(action="test")) == (True, False, "holds Contributor")


def test_edit_approve_and_document_answers_have_the_shapes_the_card_reads(world):  # noqa: F811
    record, _ = draft(world)
    world.plan_answer = plan_answer(DIGEST_B)
    edited = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "commands": 'name = "vnet-other"\nregion = "uaenorth"'}, None))
    assert edited["ok"] and set(edited["card"]) >= {"question", "endpoint", "payloads", "reference", "documents", "document_endpoint", "edit_endpoint"}
    # a refusal is a plain 200 JSON `{ok: false, error}`: the card shows `error`, and the download helper throws it
    refused = download(record)
    assert refused["ok"] is False and isinstance(refused["error"], str)
    assert decide(record, reference="CR-12345")["ok"] is False
    fresh = change_store.load(edited["change_id"])
    assert download(fresh).status_code == 200
    done = decide(fresh, reference="cr-12345")
    assert set(done["change"]) == {"id", "status", "requested_by", "approved_by", "approved_hash", "reference"}
