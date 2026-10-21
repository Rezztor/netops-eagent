"""`rolecheck.evaluate` judges the role definitions the change app holds; `run` reads them with a faked Azure."""
import copy

import pytest

from cloud_executor import arm, rolecheck
from cloud_executor.tests.support import SUB

R = rolecheck.ROLE
GOOD = {"properties": {"roleName": "Rezztor Cloud Change", "permissions": [
    {"actions": R["Actions"], "notActions": R["NotActions"], "dataActions": [], "notDataActions": []}]}}


def role(**changes):
    block = {**copy.deepcopy(GOOD["properties"]["permissions"][0]), **changes}
    return {"properties": {"roleName": "Custom", "permissions": [block]}}


def test_the_reviewed_role_is_accepted():
    assert rolecheck.evaluate([GOOD]) == ""


@pytest.mark.parametrize("definitions, fragment", [
    ([], "no role"),
    ([GOOD, role()], "more than one"),
    ([role(actions=["*"])], "wildcard"),
    ([role(actions=["*/read", "*/write", "*/delete"])], "forbidden"),
    ([role(actions=["*/read", "*/write", "*/action"])], "wildcard"),
    ([role(actions=R["Actions"] + ["Microsoft.Compute/*"])], "wildcard"),
    ([role(actions=R["Actions"] + ["Microsoft.Storage/storageAccounts/delete"])], "deletes"),
    ([role(actions=R["Actions"] + ["Microsoft.Compute/virtualMachines/restart/action"])], "forbidden"),
    ([role(actions=R["Actions"] + ["Microsoft.Network/whatever/new/action"])], "does not list"),
    ([role(notActions=R["NotActions"][1:])], "does not withhold"),
    ([role(dataActions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"])], "data actions"),
    ([{"properties": {"roleName": "Owner", "permissions": []}}], "no permissions"),
])
def test_a_role_that_is_wider_than_the_reviewed_one_is_refused(definitions, fragment):
    assert fragment in rolecheck.evaluate(definitions)


def test_the_check_matches_a_case_that_differs():
    shouting = role(actions=[a.upper() for a in R["Actions"]], notActions=[a.upper() for a in R["NotActions"]])
    assert rolecheck.evaluate([shouting]) == ""
    assert "forbidden" in rolecheck.evaluate([role(actions=R["Actions"] + ["MICROSOFT.COMPUTE/VIRTUALMACHINES/DEALLOCATE/ACTION"])])


def fake_azure(monkeypatch, definition):
    rid = f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/abc"
    seen = []

    def get(token, path):
        seen.append(path)
        if path.startswith("/subscriptions?"):
            return {"value": [{"subscriptionId": SUB}]}
        if "roleAssignments" in path:
            return {"value": [{"properties": {"roleDefinitionId": rid}}]}
        return definition

    monkeypatch.setattr(arm, "token", lambda role: "header.eyJvaWQiOiAib2lkLTEifQ.sig")  # {"oid": "oid-1"}
    monkeypatch.setattr(arm, "get", get)
    return seen


def test_run_reads_the_assignments_of_its_own_identity_and_judges_them(monkeypatch):
    seen = fake_azure(monkeypatch, GOOD)
    assert rolecheck.run() == {"role_ok": True, "reason": "", "subscriptions": [SUB]}
    assert any("principalId%20eq%20'oid-1'" in p for p in seen)
    fake_azure(monkeypatch, role(actions=["*"]))
    assert rolecheck.run()["role_ok"] is False


def test_run_says_why_when_azure_cannot_be_asked(monkeypatch):
    def refuse(role):
        raise arm.ArmError("Entra refused the applier identity: bad certificate")

    monkeypatch.setattr(arm, "token", refuse)
    answer = rolecheck.run()
    assert answer["role_ok"] is False and "bad certificate" in answer["reason"]
