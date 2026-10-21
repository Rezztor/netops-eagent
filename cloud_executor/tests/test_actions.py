"""The simple actions (`actions.yaml`, `actions.py`): the closed list, the loader's refusals, what each action checks, and the
call against a stand-in Azure. The stand-in has no delete, no stop and no restart, and a test fails if the product ever
makes a call that is not a POST or a PATCH. No network, no credential.

Request and response shapes are from Microsoft's REST reference (learn.microsoft.com/rest/api/compute/virtual-machines/start,
.../virtual-machine-scale-sets/update, .../appservice/app-service-plans/update, .../resources/tags/update-at-scope); this is
"written from documented output, not run against a tenant".
"""
import fnmatch
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from requests.structures import CaseInsensitiveDict

from cloud_executor import actions, arm, cost, rails as rails_mod, store
from cloud_executor.runner import Refusal
from cloud_executor.tests.support import SUB

HERE = Path(__file__).resolve().parents[1]
ROLE = json.loads((HERE / "role.json").read_text())
GROUP = "rg-app"
BASE = f"/subscriptions/{SUB}/resourceGroups/{GROUP}/providers"
VM = f"{BASE}/Microsoft.Compute/virtualMachines/vm-web-01"
VMSS = f"{BASE}/Microsoft.Compute/virtualMachineScaleSets/vmss-web"
PLAN = f"{BASE}/Microsoft.Web/serverfarms/plan-web"
SITE = f"{BASE}/Microsoft.Web/sites/app-web"
RAILS = {**rails_mod.defaults(), "subscriptions": [SUB], "resource_groups": {"existing": [GROUP], "new_prefix": "rg-"}}
FORBIDDEN = re.compile(r"/?\b(delete|deallocate|powerOff|restart|stop|redeploy|reimage|reset|failover|slotsswap|scale/action|"
                       r"swap|resize|purge|runCommand)\b|\bPUT\b", re.IGNORECASE)


def instance_view(code):
    return {"statuses": [{"code": "ProvisioningState/succeeded"}, {"code": code}]}


class Azure:
    """A stand-in for Azure's management plane: documents by path, every call recorded, no verb but GET, POST and PATCH."""

    def __init__(self, monkeypatch):
        self.docs: dict[str, object] = {}
        self.calls: list[tuple[str, str, object]] = []
        self.write_status, self.write_headers, self.polls, self.after_write = 200, {}, [], {}
        monkeypatch.setattr(arm, "token", lambda role: f"token-{role}")
        monkeypatch.setattr(arm, "get", self.get)
        monkeypatch.setattr(actions, "_sleep", lambda s: None)
        monkeypatch.setattr(actions.requests, "post", lambda url, **kw: self.write("POST", url, None, kw))
        monkeypatch.setattr(actions.requests, "patch", lambda url, **kw: self.write("PATCH", url, kw.get("json"), kw))
        monkeypatch.setattr(actions.requests, "get", self.poll)
        self.applied: list[dict] = []
        committed: list = []
        self.committed = committed

        def record(**kw):
            if kw["change_id"] in {a["change_id"] for a in self.applied}:
                raise store.AlreadyApplied("The change has already been applied.")
            self.applied.append(kw)

        monkeypatch.setattr(store, "record_apply", record)
        monkeypatch.setattr(store, "committed_usd", lambda: 0.0)
        monkeypatch.setattr(store, "commit_usd", lambda change, usd: committed.append((change, usd)))

    def get(self, token, path):
        self.calls.append(("GET", path, token))
        return self.docs.get(path.split("?")[0].lower())

    def put(self, path, doc):
        self.docs[path.lower()] = doc

    def write(self, method, url, body, kw):
        assert kw.get("allow_redirects") is False
        self.calls.append((method, url, body))
        for path, doc in self.after_write.items():
            self.docs[path.lower()] = doc
        return self.reply(self.write_status, self.write_headers)

    def poll(self, url, **kw):
        self.calls.append(("GET", url, "poll"))
        self.polls.append(url)
        return self.reply(200, {}, {"status": "Succeeded"})

    @staticmethod
    def reply(status, headers, body=None):
        return SimpleNamespace(status_code=status, headers=CaseInsensitiveDict(headers), json=lambda: body if body is not None else {})

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def azure(monkeypatch):
    return Azure(monkeypatch)


def body(action, resource, variables=None, **extra):
    return {"action": action, "resource_id": resource, "variables": variables or {}, "mode": "preview", "change_id": "chg-1",
            "actor": "alice", **extra}


def refusal_of(request, rails=RAILS):
    answer = actions.preview(request, rails)
    assert answer["ok"] is False, answer
    return answer["stage"], answer["refusal"]


# --- the closed list -------------------------------------------------------------------------------------------------

def test_the_list_is_exactly_the_seven_additive_actions_and_nothing_else():
    assert actions.ALLOWED == ("vm_start", "site_start", "aks_start", "pg_flexible_start", "vmss_scale_out", "plan_scale_out", "tags_merge")
    loaded = actions.load()
    assert tuple(loaded) == actions.ALLOWED
    for entry in loaded.values():
        assert (entry.method, entry.suffix) in {("POST", "/start"), ("PATCH", ""), ("PATCH", actions.TAGS_SUFFIX)}
        assert entry.kind == ("start" if entry.name.endswith("_start") else "tags" if entry.name == "tags_merge" else "scale_out")
        assert (entry.risk == "high") == (entry.kind == "scale_out")


def test_every_action_needs_only_what_the_change_role_grants_and_never_what_it_withholds():
    def matches(pattern, action):
        return fnmatch.fnmatchcase(action.lower(), pattern.lower())

    for entry in actions.load().values():
        assert any(matches(g, entry.requires) for g in ROLE["Actions"]), entry.name
        assert not any(matches(n, entry.requires) for n in ROLE["NotActions"]), entry.name


def test_the_files_name_no_other_verb_or_action_and_the_client_has_only_post_and_patch():
    source = (HERE / "actions.py").read_text()
    assert not re.search(r"requests\.(put|delete|head|options|request)\b|\.delete\(|\"DELETE\"|'DELETE'", source, re.IGNORECASE)
    assert {m.lower() for m in re.findall(r"requests\.(\w+)\(", source)} == {"post", "patch", "get"}
    text = "\n".join(line for line in (HERE / "actions.yaml").read_text().splitlines()
                     if not line.lstrip().startswith(("backout:", "#")))
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)
    for entry in yaml.safe_load(text):
        assert entry["call"]["method"] in ("POST", "PATCH") and entry["response"] == "drop"
    assert "/start" in text and not re.search(r"suffix: /(?!start|providers/Microsoft\.Resources/tags/default)", text)


@pytest.mark.parametrize("entry, why", [
    ({"name": "vm_delete"}, "not an action"),
    ({"name": "vm_restart"}, "not an action"),
    ({"name": "vm_start", "call": {"method": "DELETE", "suffix": "/start", "api_version": "2025-04-01"}}, "POST with the suffix"),
    ({"name": "vm_start", "call": {"method": "POST", "suffix": "/deallocate", "api_version": "2025-04-01"}}, "POST with the suffix"),
    ({"name": "vm_start", "call": {"method": "PUT", "suffix": "/start", "api_version": "2025-04-01"}}, "POST with the suffix"),
    ({"name": "vm_start", "call": {"method": "POST", "suffix": "/start", "api_version": "latest"}}, "dated api-version"),
    ({"name": "vmss_scale_out", "call": {"method": "PATCH", "suffix": "/scale", "api_version": "2025-04-01"}}, "PATCH with the suffix"),
    ({"name": "vmss_scale_out", "call": {"method": "PATCH", "suffix": "", "api_version": "2025-04-01"}, "risk": "low"}, "scale-out is high"),
    ({"name": "tags_merge", "call": {"method": "PATCH", "suffix": "", "api_version": "2021-04-01"}}, "PATCH with the suffix"),
])
def test_the_loader_refuses_an_entry_outside_the_contract(tmp_path, entry, why):
    good = {"risk": "low", "current": {}, "verify": {}, "requires": "x", "backout": "by hand", "response": "drop"}
    path = tmp_path / "actions.yaml"
    path.write_text(yaml.safe_dump([{**good, **entry}]))
    with pytest.raises(actions.ActionsError, match=why):
        actions.load(path)


def test_the_loader_refuses_a_missing_backout_and_a_response_that_is_kept(tmp_path):
    entry = {"name": "vm_start", "call": {"method": "POST", "suffix": "/start", "api_version": "2025-04-01"}, "risk": "low",
             "current": {}, "verify": {}, "requires": "x", "backout": "by hand", "response": "drop"}
    for broken in ({**entry, "backout": ""}, {**entry, "response": "keep"}, {**entry, "requires": ""}):
        path = tmp_path / "a.yaml"
        path.write_text(yaml.safe_dump([broken]))
        with pytest.raises(actions.ActionsError):
            actions.load(path)
    path.write_text(yaml.safe_dump([entry, entry]))
    with pytest.raises(actions.ActionsError, match="twice"):
        actions.load(path)


# --- what is asked ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("request_body, stage", [
    (body("vm_delete", VM), "gate"),
    (body("vm_start", PLAN), "gate"),                                                          # a plan is not a VM
    (body("vm_start", VM + "/extensions/x"), "gate"),                                           # a child is not the VM
    (body("vm_start", VM + "?api-version=2020-01-01"), "gate"),                                 # no query smuggled in
    (body("vm_start", f"https://evil.example{VM}"), "gate"),
    (body("vm_start", VM, {"size": "big"}), "gate"),                                            # a start takes no values
    (body("vm_start", VM.replace(SUB, "99999999-9999-9999-9999-999999999999")), "policy"),     # a subscription not allowed
    (body("vm_start", VM.replace(GROUP, "rg-other")), "policy"),                                # a group not allowed
    (body("vmss_scale_out", VMSS, {"capacity": 0}), "gate"),
    (body("vmss_scale_out", VMSS, {"capacity": True}), "gate"),
    (body("vmss_scale_out", VMSS, {"capacity": 5, "size": "Standard_D64s_v5"}), "gate"),      # the size never changes
    (body("tags_merge", VM, {"tags": {}}), "gate"),
    (body("tags_merge", VM, {"tags": {"env": ""}}), "gate"),                                    # a tag is never emptied
    (body("tags_merge", VM, {"tags": {"bad key!": "x"}}), "gate"),
    (body("tags_merge", VM, {"tags": {"env": "a<b"}}), "gate"),
])
def test_a_request_outside_the_contract_is_refused_before_azure_is_read(azure, request_body, stage):
    assert refusal_of(request_body)[0] == stage
    assert azure.calls == []


# --- start -----------------------------------------------------------------------------------------------------------

def test_a_start_previews_the_one_post_and_gives_the_same_digest_every_time(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    first, second = actions.preview(body("vm_start", VM), RAILS), actions.preview(body("vm_start", VM), RAILS)
    assert first["ok"] and first == second and re.fullmatch(r"[0-9a-f]{64}", first["plan_digest"])
    assert first["line"] == f"POST https://management.azure.com{VM}/start?api-version=2025-04-01"
    assert first["current"] == {"state": "PowerState/deallocated"} and first["risk"] == "low"
    assert first["summary"]["add"] == 0 and first["summary"]["change"] == 1 and first["summary"]["lines"][0].startswith("~ vm-web-01")
    assert first["summary"]["resources"][0]["action"] == "start" and "backout" in first
    assert azure.writes() == [] and {c[2] for c in azure.calls} == {"token-planner"}  # a preview reads, as the read identity


def test_a_stopped_but_not_deallocated_vm_can_be_started_and_a_running_one_cannot(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/stopped"))
    assert actions.preview(body("vm_start", VM), RAILS)["ok"]
    azure.put(VM + "/instanceView", instance_view("PowerState/running"))
    assert "nothing to start" in refusal_of(body("vm_start", VM))[1]
    azure.docs.clear()
    assert refusal_of(body("vm_start", VM))[0] == "read"


def test_the_digest_changes_when_azure_changed_under_the_draft(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    before = actions.preview(body("vm_start", VM), RAILS)["plan_digest"]
    azure.put(VM + "/instanceView", instance_view("PowerState/stopped"))
    assert actions.preview(body("vm_start", VM), RAILS)["plan_digest"] != before


def test_each_start_reads_its_own_state_and_calls_its_own_url(azure):
    azure.put(SITE, {"properties": {"state": "Stopped"}})
    answer = actions.preview(body("site_start", SITE), RAILS)
    assert answer["line"] == f"POST https://management.azure.com{SITE}/start?api-version=2024-04-01"
    aks = f"{BASE}/Microsoft.ContainerService/managedClusters/aks-1"
    azure.put(aks, {"properties": {"powerState": {"code": "Stopped"}}})
    assert actions.preview(body("aks_start", aks), RAILS)["ok"]
    pg = f"{BASE}/Microsoft.DBforPostgreSQL/flexibleServers/pg-1"
    azure.put(pg, {"properties": {"state": "Ready"}})
    assert "nothing to start" in refusal_of(body("pg_flexible_start", pg))[1]


# --- scale out -------------------------------------------------------------------------------------------------------

@pytest.fixture
def priced(monkeypatch):
    monkeypatch.setattr(cost, "_lookup", lambda: lambda region, service, sku, meter="", os="": {"usd_month": 50.0})


def test_a_scale_out_must_be_higher_keeps_the_size_and_is_priced(azure, priced):
    azure.put(VMSS, {"location": "uaenorth", "sku": {"name": "Standard_D2s_v5", "tier": "Standard", "capacity": 2}})
    answer = actions.preview(body("vmss_scale_out", VMSS, {"capacity": 4}), RAILS)
    assert answer["ok"] and answer["risk"] == "high" and answer["cost"]["monthly_usd"] == 100.0
    assert answer["line"].startswith(f"PATCH https://management.azure.com{VMSS}?api-version=2025-04-01 ")
    assert json.loads(answer["line"].split("?api-version=2025-04-01 ", 1)[1]) == {
        "sku": {"name": "Standard_D2s_v5", "tier": "Standard", "capacity": 4}}
    assert answer["current"] == {"state": "2"} and "unchanged" in answer["summary"]["lines"][0]
    for same_or_lower in (2, 1):
        stage, text = refusal_of(body("vmss_scale_out", VMSS, {"capacity": same_or_lower}))
        assert stage == "policy" and "scale-in" in text
    assert "at most 20" in refusal_of(body("vmss_scale_out", VMSS, {"capacity": 40}))[1]
    assert azure.writes() == []


def test_a_scale_out_is_refused_over_the_cost_ceiling_and_when_no_price_is_found(azure, priced, monkeypatch):
    azure.put(PLAN, {"location": "uaenorth", "sku": {"name": "P1v3", "capacity": 1}})
    assert refusal_of(body("plan_scale_out", PLAN, {"capacity": 10}))[0] == "cost"          # 9 x 50 over the 250 ceiling
    monkeypatch.setattr(cost, "_lookup", lambda: None)
    stage, text = refusal_of(body("plan_scale_out", PLAN, {"capacity": 2}))
    assert stage == "cost" and "unknown" in text


# --- tags ------------------------------------------------------------------------------------------------------------

def test_a_tag_merge_sends_only_what_changes_and_never_removes_one(azure):
    azure.put(VM + actions.TAGS_SUFFIX, {"properties": {"tags": {"env": "prod", "owner": "netops"}}})
    answer = actions.preview(body("tags_merge", VM, {"tags": {"env": "prod", "costcentre": "cc-1", "owner": "infra"}}), RAILS)
    sent = json.loads(answer["line"].split("?api-version=2021-04-01 ", 1)[1])
    assert sent == {"operation": "Merge", "properties": {"tags": {"costcentre": "cc-1", "owner": "infra"}}}
    assert any("netops -> infra" in line for line in answer["summary"]["lines"])
    assert any(c.get("level") == "warn" for c in answer["checks"])
    assert "already has those tags" in refusal_of(body("tags_merge", VM, {"tags": {"env": "prod"}}))[1]
    group = f"/subscriptions/{SUB}/resourceGroups/{GROUP}"
    azure.put(group + actions.TAGS_SUFFIX, {"properties": {"tags": {}}})
    assert actions.preview(body("tags_merge", group, {"tags": {"env": "test"}}), RAILS)["ok"]


def test_a_tag_merge_cannot_pass_fifty_tags(azure):
    azure.put(VM + actions.TAGS_SUFFIX, {"properties": {"tags": {f"t{i}": "x" for i in range(50)}}})
    assert "more than 50" in refusal_of(body("tags_merge", VM, {"tags": {"one-more": "x"}}))[1]


# --- run -------------------------------------------------------------------------------------------------------------

def run_body(preview_answer, **extra):
    return {**preview_answer["request"], "mode": "run", "plan_digest": preview_answer["plan_digest"], "reference": "cr-12345", **extra}


def prepared(azure, action="vm_start", resource=VM, variables=None):
    request = body(action, resource, variables)
    answer = actions.preview(request, RAILS)
    assert answer["ok"], answer
    return {**answer, "request": request}


def test_a_start_posts_once_waits_on_the_management_host_and_verifies_as_the_read_identity(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.write_status = 202
    azure.write_headers = {"Location": "https://management.azure.com/operations/1", "Retry-After": "2"}
    azure.after_write = {VM + "/instanceView": instance_view("PowerState/running")}
    result = actions.run(run_body(ready), RAILS)
    assert result == {"status": "applied", "verified": True, "changed_ids": []}
    assert [(m, u) for m, u, _ in azure.writes()] == [("POST", f"https://management.azure.com{VM}/start?api-version=2025-04-01")]
    assert azure.polls == ["https://management.azure.com/operations/1"]
    assert azure.applied[0]["reference"] == "CR-12345" and azure.applied[0]["plan_digest"] == ready["plan_digest"]
    assert azure.applied[0]["code"] == ready["line"] and azure.applied[0]["state_name"] == ""
    assert {m for m, *_ in azure.calls} <= {"GET", "POST", "PATCH"}
    assert [c[2] for c in azure.calls if c[0] == "POST"] == [None]


def test_an_async_operation_that_fails_is_a_failed_job_and_nothing_is_verified(azure, monkeypatch):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.write_status = 202
    azure.write_headers = {"Azure-AsyncOperation": "https://management.azure.com/operations/2"}
    monkeypatch.setattr(actions.requests, "get", lambda url, **kw: azure.reply(200, {}, {"status": "Failed"}))
    result = actions.run(run_body(ready), RAILS)
    assert result["status"] == "failed" and "Failed" in result["error"]


def test_a_redirect_or_a_poll_url_on_another_host_is_not_followed(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.write_status = 202
    for url in ("https://evil.example/operations/1", "http://management.azure.com/operations/1", "https://user@management.azure.com/x", ""):
        azure.write_headers = {"Location": url}
        azure.applied.clear()
        result = actions.run(run_body(ready), RAILS)
        assert result["status"] == "failed" and "management host" in result["error"], url
    assert azure.polls == []


def test_a_digest_that_is_not_the_approved_one_is_superseded_and_nothing_is_sent(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.put(VM + "/instanceView", instance_view("PowerState/stopped"))      # Azure moved after the approval
    result = actions.run(run_body(ready), RAILS)
    assert result["status"] == "superseded" and azure.writes() == [] and azure.applied == []


def test_a_change_runs_once(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.after_write = {VM + "/instanceView": instance_view("PowerState/running")}
    assert actions.run(run_body(ready), RAILS)["status"] == "applied"
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))   # by hand, in the portal
    again = actions.run(run_body(ready), RAILS)
    assert again["status"] == "failed" and "already been applied" in again["error"] and len(azure.writes()) == 1


def test_azures_refusal_is_a_failed_job_with_its_code_and_no_verify(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    azure.write_status = 403
    azure.reply = lambda status, headers, body=None: SimpleNamespace(
        status_code=status, headers=CaseInsensitiveDict(headers), json=lambda: {"error": {"code": "AuthorizationFailed", "message": "no"}})
    result = actions.run(run_body(ready), RAILS)
    assert result["status"] == "failed" and "403" in result["error"] and "AuthorizationFailed" in result["error"]
    assert not [c for c in azure.calls[azure.calls.index(azure.writes()[0]) + 1:] if c[0] == "GET"]


def test_a_state_that_does_not_read_back_as_intended_is_verify_failed(azure, monkeypatch):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    monkeypatch.setattr(actions, "WAIT_SECONDS", 0)
    result = actions.run(run_body(ready), RAILS)
    assert result["status"] == "verify_failed" and result["verified"] is False


def test_a_scale_out_patches_the_count_commits_its_cost_and_verifies_the_count(azure, priced):
    azure.put(VMSS, {"location": "uaenorth", "sku": {"name": "Standard_D2s_v5", "tier": "Standard", "capacity": 2}})
    ready = prepared(azure, "vmss_scale_out", VMSS, {"capacity": 3})
    azure.after_write = {VMSS: {"sku": {"name": "Standard_D2s_v5", "capacity": 3}}}
    result = actions.run(run_body(ready), RAILS)
    assert result["status"] == "applied"
    (method, url, sent), = azure.writes()
    assert method == "PATCH" and sent == {"sku": {"name": "Standard_D2s_v5", "tier": "Standard", "capacity": 3}}
    assert azure.committed == [("chg-1", 50.0)]


def test_a_tag_merge_verifies_every_tag_it_sent(azure):
    azure.put(VM + actions.TAGS_SUFFIX, {"properties": {"tags": {"env": "prod"}}})
    ready = prepared(azure, "tags_merge", VM, {"tags": {"owner": "netops"}})
    azure.after_write = {VM + actions.TAGS_SUFFIX: {"properties": {"tags": {"env": "prod", "owner": "netops"}}}}
    assert actions.run(run_body(ready), RAILS)["status"] == "applied"
    assert azure.writes()[0][2] == {"operation": "Merge", "properties": {"tags": {"owner": "netops"}}}


def test_a_run_inside_a_scope_that_was_withdrawn_since_the_draft_is_refused(azure):
    azure.put(VM + "/instanceView", instance_view("PowerState/deallocated"))
    ready = prepared(azure)
    result = actions.run(run_body(ready), {**RAILS, "subscriptions": []})
    assert result["status"] == "failed" and "not one this product may change" in result["error"] and azure.writes() == []
