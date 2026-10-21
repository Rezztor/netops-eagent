"""The API: who may call what, what a request must carry, and the guard rails and key routes. The runner is the real
one (with the stand-in `tofu`), so a plan and an apply go through the same code a deployment runs."""
import time

import pytest

from cloud_executor import app as cloud_app, arm, rails, rolecheck, runner, store, templates
from cloud_executor.tests.support import RAILS, RG_SOURCE, RESOURCE_GROUP, SUB

JOB, ADMIN = {"Authorization": "Bearer job-token"}, {"Authorization": "Bearer admin-token"}


@pytest.fixture
def client(cloud, monkeypatch):
    monkeypatch.setattr(cloud_app, "_catalogue", None)
    rails.save({**RAILS, "resource_types": ["azurerm_resource_group"]}, [])
    monkeypatch.setattr(cloud_app, "_submissions", cloud_app.deque())
    monkeypatch.setattr(store, "fail_interrupted", lambda: 0)
    return cloud_app.app.test_client()


def plan_body(**extra):
    return {"mode": "apply", "source": RG_SOURCE, "workspace": RESOURCE_GROUP, "change_id": "chg-1", "actor": "alice", **extra}


def test_healthz_needs_no_token_and_holds_no_secret(client):
    answer = client.get("/healthz")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] is True and body["configured"] is True
    assert body["tofu"] == "1.13.1" and body["templates_commit"] == "unpinned" and "resource_group" in body["templates"]
    assert "token" not in answer.get_data(as_text=True).lower() and "password" not in answer.get_data(as_text=True).lower()


def test_healthz_reads_the_provider_versions_from_the_mirror(client, tmp_path, monkeypatch):
    for path, version in (("hashicorp/azurerm", "5.8.0"), ("azure/azapi", "2.13.0")):
        (tmp_path / "registry.opentofu.org" / path / version / "linux_amd64").mkdir(parents=True)
    monkeypatch.setenv("REZZTOR_CLOUD_MIRROR", str(tmp_path))
    assert client.get("/healthz").get_json()["providers"] == {"azurerm": "5.8.0", "azapi": "2.13.0"}


def test_a_token_with_a_letter_outside_ascii_is_refused_like_any_wrong_token(client):
    """`hmac.compare_digest` raises on a str that is not ASCII: one such byte in the header was a 500, not a 401."""
    wrong = {"Authorization": "Bearer caf\xe9"}
    assert client.post("/v1/plan", json=plan_body(), headers=wrong).status_code == 401
    assert client.get("/v1/admin/keys", headers=wrong).status_code == 401


@pytest.mark.parametrize("path, method", [("/v1/plan", "post"), ("/v1/apply", "post"), ("/v1/action", "post"),
                                          ("/v1/refresh/accept", "post"), ("/v1/adopt/apply", "post"), ("/v1/plan-set", "post"),
                                          ("/v1/jobs/" + "0" * 32, "get")])
def test_the_job_plane_wants_the_job_token_and_the_admin_token_is_not_enough(client, path, method):
    send = getattr(client, method)
    assert send(path, json={}).status_code == 401
    assert send(path, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert send(path, json={}, headers=ADMIN).status_code == 401


@pytest.mark.parametrize("path, method", [("/v1/admin/guardrails", "get"), ("/v1/admin/keys", "get"),
                                          ("/v1/admin/test", "post"), ("/v1/admin/keys/planner", "post")])
def test_the_admin_plane_wants_the_admin_token_and_the_job_token_is_not_enough(client, path, method):
    send = getattr(client, method)
    assert send(path).status_code == 401
    assert send(path, headers=JOB).status_code == 401


def test_no_token_configured_refuses_everything_and_so_do_equal_tokens(client, monkeypatch):
    monkeypatch.setenv("REZZTOR_CLOUD_ADMIN_TOKEN", "job-token")
    assert client.post("/v1/plan", json=plan_body(), headers=JOB).status_code == 401
    assert client.get("/v1/admin/keys", headers=JOB).status_code == 401
    monkeypatch.delenv("REZZTOR_CLOUD_JOB_TOKEN")
    monkeypatch.setenv("REZZTOR_CLOUD_ADMIN_TOKEN", "admin-token")
    refused = client.post("/v1/plan", json=plan_body(), headers=JOB)
    assert refused.status_code == 401 and "no token" in refused.get_json()["error"]


def test_a_plan_over_http_answers_with_the_digest_and_a_refusal_is_an_answer(client):
    ok = client.post("/v1/plan", json=plan_body(), headers=JOB)
    assert ok.status_code == 200 and ok.get_json()["ok"] and len(ok.get_json()["plan_digest"]) == 64
    refused = client.post("/v1/plan", json=plan_body(mode="destroy"), headers=JOB)
    assert refused.status_code == 200 and refused.get_json()["ok"] is False and refused.get_json()["stage"] == "gate"
    assert client.post("/v1/plan", json={"workspace": RESOURCE_GROUP}, headers=JOB).status_code == 400


def wait_for(client, job_id):
    for _ in range(100):
        record = client.get(f"/v1/jobs/{job_id}", headers=JOB).get_json()
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def test_apply_needs_the_number_and_runs_as_a_job(client, cloud):
    digest = client.post("/v1/plan", json=plan_body(), headers=JOB).get_json()["plan_digest"]
    for reference in (None, "", "CR 12", "CR-12", "x" * 41, "not a number"):
        body = plan_body(plan_digest=digest, **({} if reference is None else {"reference": reference}))
        assert client.post("/v1/apply", json=body, headers=JOB).status_code == 400
    assert "apply" not in [c["args"][0] for c in cloud.calls()]
    accepted = client.post("/v1/apply", json=plan_body(plan_digest=digest, reference="cr-1234"), headers=JOB)
    assert accepted.status_code == 202
    record = wait_for(client, accepted.get_json()["job_id"])
    assert record["status"] == "applied" and record["verified"] is True and record["ended_at"] >= record["started_at"]
    assert cloud.applied[0]["reference"] == "CR-1234"
    again = client.post("/v1/apply", json=plan_body(plan_digest=digest, reference="CR-1234"), headers=JOB)
    assert again.status_code == 409


def test_apply_refuses_a_digest_that_is_not_a_digest_and_a_source_the_gate_refuses(client):
    assert client.post("/v1/apply", json=plan_body(plan_digest="abc", reference="CR-1234"), headers=JOB).status_code == 400
    bad = plan_body(plan_digest="a" * 64, reference="CR-1234", source={"template": "nope", "variables": {}})
    assert client.post("/v1/apply", json=bad, headers=JOB).status_code == 422


def test_a_job_that_does_not_exist_is_a_404_and_an_empty_action_is_refused(client):
    assert client.get("/v1/jobs/" + "0" * 32, headers=JOB).status_code == 404
    assert client.get("/v1/jobs/../etc/passwd", headers=JOB).status_code == 404
    assert client.post("/v1/action", json={}, headers=JOB).status_code == 400


def test_a_seventh_request_in_a_minute_is_refused(client):
    codes = [client.post("/v1/plan", json=plan_body(), headers=JOB).status_code for _ in range(7)]
    assert codes == [200] * 6 + [429]


def test_guardrails_are_read_and_saved_with_the_admin_token_and_deleting_is_not_one_of_them(client):
    got = client.get("/v1/admin/guardrails", headers=ADMIN).get_json()
    assert got["reference_pattern"] == r"(CR|SR)-?[0-9]{4,10}" and "deletes_allowed" not in got
    saved = client.put("/v1/admin/guardrails", json={"regions": ["uaenorth"], "max_resources": 5}, headers=ADMIN)
    assert saved.status_code == 200 and saved.get_json()["max_resources"] == 5
    for bad in ({"deletes_allowed": 1}, {"reference_pattern": ".*"}, {"reference_pattern": "("}, {"document_valid_days": 99}):
        assert client.put("/v1/admin/guardrails", json=bad, headers=ADMIN).status_code == 400


def test_a_key_is_made_inside_and_only_the_certificate_comes_back(client):
    answer = client.post("/v1/admin/keys/planner", headers=ADMIN, json={
        "tenant_id": "99999999-8888-7777-6666-555555555555", "client_id": "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee"})
    body = answer.get_json()
    assert answer.status_code == 200 and body["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE" not in answer.get_data(as_text=True) and len(body["thumbprint"]) == 40
    status = client.get("/v1/admin/keys", headers=ADMIN).get_json()
    assert status["planner"]["client_id"] == "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee" and status["passphrase_set"] is True
    assert client.post("/v1/admin/keys/owner", headers=ADMIN, json={}).status_code == 400


def test_the_test_route_reports_both_identities_and_the_role_and_returns_no_token(client, monkeypatch):
    monkeypatch.setattr(arm, "token", lambda role: "tok-" + role)
    monkeypatch.setattr(arm, "get", lambda token, path: {"value": [{"subscriptionId": SUB, "displayName": "prod"}]})
    monkeypatch.setattr(rolecheck, "run", lambda role="applier": {"role_ok": False, "reason": "The role grants *."})
    answer = client.post("/v1/admin/test", headers=ADMIN)
    body = answer.get_json()
    assert body["planner"]["subscriptions"] == [{"id": SUB, "name": "prod"}] and body["role_ok"] is False
    assert "tok-" not in answer.get_data(as_text=True)


def test_an_apply_left_running_when_the_executor_stopped_is_failed_at_the_next_start(cloud):
    job_id = store.new_job("chg-9", "alice")
    store.update(job_id, status="running")
    assert store.fail_interrupted() == 1
    assert store.get(job_id)["status"] == "failed" and "stopped" in store.get(job_id)["error"]
