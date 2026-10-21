"""The runner against a stand-in `tofu` that replays recorded plan JSON: what it runs, in what environment, and what
it refuses. No network, no database, no Azure. Fixture plans are written from documented output, not recorded from a run."""
import base64
import copy
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import pkcs12

from cloud_executor import rails, runner, templates
from cloud_executor.tests.support import RAILS, RESOURCE_GROUP, RG_SOURCE, SUB, plan_fixture, request

SOURCE_DIR = Path(__file__).resolve().parents[1]
CATALOGUE = templates.load()
EXISTING_IDS = runner.existing_ids  # the fixture replaces it on the module; these tests want the real one
ALLOWED = {"version", "init", "workspace", "validate", "plan", "show", "apply"}


def good_rails(**over):
    return rails.validate({**RAILS, "resource_types": ["azurerm_resource_group"], **over}, [])


def run_plan(**extra):
    return runner.plan(request(**extra), good_rails(), CATALOGUE)


def test_a_plan_runs_the_steps_in_order_and_leaves_nothing_behind(cloud):
    answer = run_plan()
    assert answer["ok"], answer
    assert re.fullmatch(r"[0-9a-f]{64}", answer["plan_digest"])
    assert answer["summary"]["add"] == 1 and answer["summary"]["change"] == 0
    assert answer["cost"]["monthly_usd"] == 0 and answer["cost"]["unknown"] == []
    steps = [c["args"][0] for c in cloud.calls()]
    assert steps == ["init", "workspace", "validate", "plan", "show"]
    assert "plan.bin" in cloud.calls()[-1]["files"]  # the plan existed when it was read...
    assert list(cloud.runs.iterdir()) == []  # ...and the directory, the plan file with it, is gone


def test_the_environment_is_built_from_scratch(cloud):
    run_plan()
    env = cloud.calls()[0]["env"]
    assert set(env) == {
        "PATH", "HOME", "TMPDIR", "TF_DATA_DIR", "TF_IN_AUTOMATION", "TF_INPUT", "TF_CLI_CONFIG_FILE", "TF_ENCRYPTION",
        "ARM_TENANT_ID", "ARM_CLIENT_ID", "ARM_CLIENT_CERTIFICATE", "ARM_USE_CLI", "ARM_USE_MSI", "ARM_USE_OIDC",
        "ARM_USE_AKS_WORKLOAD_IDENTITY", "PG_CONN_STR", "LC_CTYPE"} | (set(env) & {"LC_CTYPE", "LANG"})
    assert env["ARM_USE_CLI"] == "false" and env["TF_CLI_CONFIG_FILE"] == "/etc/tofu/cli.tfrc"
    assert not any(k.startswith(("TF_VAR_", "TF_CLI_ARGS")) for k in env)
    assert "enforced = true" in env["TF_ENCRYPTION"] and "p" * 24 in env["TF_ENCRYPTION"]
    assert env["PG_CONN_STR"].startswith("postgresql://eagent_cloud:db-secret-password@postgres:5432/eagent")
    key, cert, _ = pkcs12.load_key_and_certificates(base64.b64decode(env["ARM_CLIENT_CERTIFICATE"]), None)
    assert key is not None and cert is not None  # a real PKCS#12, made in memory


def test_the_applier_and_the_planner_sign_in_as_themselves(cloud):
    run_plan()
    assert cloud.calls()[0]["env"]["ARM_CLIENT_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    runner.apply(request(plan_digest=run_plan()["plan_digest"], reference="CR-1234"), good_rails(), CATALOGUE)
    assert cloud.calls()[-1]["env"]["ARM_CLIENT_ID"] == "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_the_same_code_against_the_same_azure_gives_the_same_digest(cloud):
    assert run_plan()["plan_digest"] == run_plan()["plan_digest"]
    other = copy.deepcopy(RG_SOURCE)
    other["variables"]["tags"]["project"] = "other"
    changed = plan_fixture()
    changed["resource_changes"][0]["change"]["after"]["tags"]["project"] = "other"
    cloud.script(plan=changed)
    assert run_plan(source=other)["plan_digest"] != run_plan()["plan_digest"]


def test_only_these_subcommands_are_ever_run_and_none_removes_anything(cloud):
    run_plan()
    runner.apply(request(plan_digest=run_plan()["plan_digest"], reference="CR-1234"), good_rails(), CATALOGUE)
    for call in cloud.calls():
        assert call["args"][0] in ALLOWED
        assert not any("destroy" in a for a in call["args"])


def test_the_executor_source_never_names_the_command_that_removes():
    for path in SOURCE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "destroy" not in text, f"{path.name} names the command that removes what OpenTofu made"
    arm = (SOURCE_DIR / "arm.py").read_text(encoding="utf-8")
    assert sorted(re.findall(r"requests\.(\w+)\(", arm)) == ["get", "post"], "arm.py reads, and signs in, and does nothing else"
    assert not re.search(r"(?i)\bdelete\b", arm)


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["forget"]])
def test_a_plan_that_removes_or_replaces_is_refused_and_never_applied(cloud, actions):
    bad = plan_fixture("resource_group", update=True)
    bad["resource_changes"][0]["change"]["actions"] = actions
    cloud.script(plan=bad)
    answer = run_plan()
    assert answer["ok"] is False and answer["stage"] == "policy"
    done = runner.apply(request(plan_digest="0" * 64, reference="CR-1234"), good_rails(), CATALOGUE)
    assert done["status"] == "failed" and "apply" not in [c["args"][0] for c in cloud.calls()]


@pytest.mark.parametrize("stage", ["init", "validate", "plan"])
def test_a_failed_step_is_a_refusal_at_that_stage_and_the_error_holds_no_secret(cloud, stage):
    cloud.script(fail={stage: "boom postgresql://eagent_cloud:db-secret-password@postgres " + "p" * 24})
    answer = run_plan()
    assert answer["ok"] is False and answer["stage"] == stage
    assert "db-secret-password" not in answer["refusal"] and "p" * 24 not in answer["refusal"]


@pytest.mark.parametrize("change", [
    {"workspace": {"subscription": "00000000-0000-0000-0000-000000000000", "resource_group": "rg-x"}},
    {"workspace": {"subscription": SUB, "resource_group": "bad name!"}},
    {"source": {"template": "no_such_template", "variables": {}}},
    {"source": {"template": "resource_group", "variables": {"region": "uaenorth"}}},
    {"source": {"code": 'resource "azurerm_resource_group" "x" {}'}},  # free code is off
    {"source": {"template": "resource_group", "code": "x"}},
    {"mode": "destroy"},
])
def test_a_request_the_gate_refuses_never_reaches_tofu(cloud, change):
    answer = run_plan(**change)
    assert answer["ok"] is False and answer["stage"] == "gate"
    assert cloud.calls() == []


def test_free_code_is_checked_by_the_static_gate_even_when_switched_on(cloud):
    on = good_rails(allow_free_code=True)
    answer = runner.plan(request(source={"code": 'provisioner "local-exec" {}'}), on, CATALOGUE)
    assert answer["ok"] is False and answer["stage"] == "gate" and cloud.calls() == []


def test_a_plan_in_import_mode_must_change_nothing(cloud):
    assert run_plan(mode="import")["stage"] == "policy"


def test_apply_plans_again_and_applies_only_the_approved_digest(cloud):
    digest = run_plan()["plan_digest"]
    done = runner.apply(request(plan_digest=digest, reference="cr-1234"), good_rails(), CATALOGUE)
    assert done == {"status": "applied", "verified": True, "changed_ids": []}
    assert [c["args"][0] for c in cloud.calls()][-3:] == ["apply", "show", "plan"]
    assert cloud.calls()[-1]["args"][1] == "-detailed-exitcode"
    row = cloud.applied[0]
    assert row["reference"] == "CR-1234" and row["plan_digest"] == digest and row["change_id"] == "chg-1"
    assert row["state_name"].startswith("w") and row["workspace"].startswith(SUB)


def test_a_different_digest_is_superseded_and_nothing_is_applied(cloud):
    done = runner.apply(request(plan_digest="f" * 64, reference="CR-1234"), good_rails(), CATALOGUE)
    assert done["status"] == "superseded" and cloud.applied == []
    assert "apply" not in [c["args"][0] for c in cloud.calls()]


def test_a_change_applies_once(cloud):
    digest = run_plan()["plan_digest"]
    assert runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)["status"] == "applied"
    again = runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)
    assert again["status"] == "failed" and "already" in again["error"]


def test_a_pending_difference_after_the_apply_is_verify_failed_and_a_failed_apply_is_failed(cloud):
    digest = run_plan()["plan_digest"]
    cloud.script(detailed_exitcode=2)
    assert runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)["status"] == "verify_failed"
    cloud.script(detailed_exitcode=0, fail={"apply": "azure said no"})
    failed = runner.apply(request(plan_digest=digest, reference="CR-1234", change_id="chg-2"), good_rails(), CATALOGUE)
    assert failed["status"] == "failed" and "azure said no" in failed["error"]


def test_the_ids_of_what_was_made_come_from_the_state(cloud):
    rid = f"/subscriptions/{SUB}/resourceGroups/rg-payments-prod-uaen"
    cloud.script(state={"values": {"root_module": {"resources": [
        {"address": "azurerm_resource_group.this", "values": {"id": rid}},
        {"address": "azurerm_other.x", "values": {"id": "/subscriptions/other"}}]}}})
    digest = run_plan()["plan_digest"]
    done = runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)
    assert done["changed_ids"] == [rid]


def test_a_cost_ceiling_stops_an_apply_before_anything_is_recorded(cloud):
    digest = run_plan()["plan_digest"]
    runner.store.commit_usd("earlier", 5000)
    done = runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)
    assert done["status"] == "failed" and "cost" in done["error"] and cloud.applied == []


def test_the_plan_file_is_gone_as_soon_as_it_is_read_but_an_apply_keeps_it_until_applied(cloud):
    run_plan()
    from pathlib import Path

    assert not list(Path(cloud.runs).glob("*/plan.bin"))
    digest = run_plan()["plan_digest"]
    runner.apply(request(plan_digest=digest, reference="CR-1234"), good_rails(), CATALOGUE)
    assert "plan.bin" in [c for c in cloud.calls() if c["args"][0] == "apply"][0]["files"]


def test_what_exists_is_asked_of_azure_with_the_planner_key_and_added_to_what_the_caller_sends(cloud, monkeypatch):
    seen = []

    def get(token, path):
        seen.append((token, path))
        if "nextLink" not in seen[0][1] and len(seen) == 1:
            return {"value": [{"id": "/subscriptions/S/resourceGroups/G/providers/X/a"}],
                    "nextLink": runner.arm.MANAGEMENT + "/subscriptions/S/resourcegroups/G/resources?page=2"}
        if len(seen) == 2:
            return {"value": [{"id": "/subscriptions/S/resourceGroups/G/providers/X/B"}], "nextLink": "https://evil.example/next"}
        raise AssertionError("a link to another host was followed")

    monkeypatch.setattr(runner.arm, "token", lambda role: f"token-{role}")
    monkeypatch.setattr(runner.arm, "get", get)
    found = EXISTING_IDS("S", "G", ["/Subscriptions/S/extra"])
    assert found == {"/subscriptions/s/resourcegroups/g/providers/x/a", "/subscriptions/s/resourcegroups/g/providers/x/b",
                     "/subscriptions/s/extra"}
    assert {t for t, _ in seen} == {"token-planner"}


def test_a_listing_that_fails_refuses_the_plan_instead_of_guessing(cloud, monkeypatch):
    def refuse(role):
        raise runner.arm.ArmError("Entra refused the planner identity")

    monkeypatch.setattr(runner.arm, "token", refuse)
    monkeypatch.setattr(runner, "existing_ids", EXISTING_IDS)
    answer = run_plan()
    assert answer["ok"] is False and answer["stage"] == "policy" and "what already exists" in answer["refusal"]


# ----------------------------------------------------------------- a step of a set, applied (ADR 0073 to 0075)

def step_request(step, change_id, **extra):
    return request(source={"template": step["template"], "variables": step["variables"]}, change_id=change_id,
                   workspace={"subscription": SUB, "resource_group": step["resource_group"]}, **extra)


def test_a_step_applies_into_a_group_the_executor_made_and_into_no_other(cloud):
    from cloud_executor.tests.test_plan_set import good_rails as set_rails, vnet_step

    step, step_plan = vnet_step("rg-made", "vnet-hub", "10.0.0.0/16")
    cloud.script(plan=step_plan)
    asked = step_request(step, "set-1.02")
    assert runner.plan(asked, set_rails(), CATALOGUE)["stage"] == "policy"  # neither listed nor made
    cloud.applied.append({"workspace": f"{SUB}/rg-made/resource_group/abc", "change_id": "set-1.01"})
    assert runner.plan(asked, set_rails(), CATALOGUE)["stage"] == "policy"  # a row, but the apply never reached the ledger
    runner.store.commit_usd("set-1.01", 0)
    digest = runner.plan(asked, set_rails(), CATALOGUE)["plan_digest"]
    done = runner.apply({**asked, "plan_digest": digest, "reference": "CR-1234"}, set_rails(), CATALOGUE)
    assert done["status"] == "applied" and cloud.applied[-1]["change_id"] == "set-1.02"
    other = vnet_step("rg-other", "vnet-x", "10.9.0.0/16")
    cloud.script(plan=other[1])
    denied = runner.apply({**step_request(other[0], "set-1.03"), "plan_digest": digest, "reference": "CR-1234"},
                          set_rails(), CATALOGUE)
    assert denied["status"] == "failed" and "not one this product may change" in denied["error"]


def test_a_step_whose_parent_is_not_in_azure_is_refused_at_apply_and_nothing_is_applied(cloud, monkeypatch):
    from cloud_executor.tests.test_plan_set import good_rails as set_rails, subnet_step

    step, step_plan = subnet_step("rg-live", "vnet-live", "snet-a", "10.0.1.0/24")
    cloud.script(plan=step_plan)
    live = set_rails(resource_groups={"existing": ["rg-live"], "new_prefix": "rg-"})
    asked = step_request(step, "set-2.01", plan_digest="a" * 64, reference="CR-1234")
    present, seen = [], []
    monkeypatch.setattr(runner.arm, "token", lambda role: f"token-{role}")
    monkeypatch.setattr(runner.arm, "get", lambda token, path: seen.append((token, path)) or ({"id": path} if present else None))
    done = runner.apply(asked, live, CATALOGUE)
    assert done["status"] == "failed" and "vnet-live does not exist, and no earlier step makes it." in done["error"]
    assert "apply" not in [c["args"][0] for c in cloud.calls()] and cloud.applied == []
    assert seen == [("token-planner", f"/subscriptions/{SUB}/resourcegroups/rg-live/providers/microsoft.network/virtualnetworks/vnet-live?api-version=2024-05-01")]
    present.append(1)  # now it exists: the same request gets as far as the digest
    assert runner.apply(asked, live, CATALOGUE)["status"] == "superseded"
