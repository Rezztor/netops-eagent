"""The executor's durable job history.

The executor used to forget every job on restart, which was defensible while the
agent was its only caller. A console makes it indefensible: "what did we push to
that switch last Tuesday" is the first question anyone asks, and the application's
audit trail cannot answer it -- it records that a job ran and who authorised it,
not the per-task commands and diffs the device reported.

These tests are about the properties that make the store trustworthy rather than
about SQLite: it survives a restart, it does not grow without bound, its filters
cannot be turned into a query language, and the fields it stores purely as labels
never become inputs to Ansible.
"""

from __future__ import annotations

import time

import pytest

from executor import app as executor_app
from executor import store as executor_store
from plugins._ea_ansible.helpers import job_schema

from plugins._ea_ansible.tests.conftest import AUTH, TOKEN


def _settle(client, job_id: str, seconds: float = 5.0) -> dict:
    """Wait for a job to reach a terminal state.

    202 means admitted, not finished -- the work happens on another thread, so a
    test that asserts on the outcome has to wait for it rather than assume the
    scheduler got there first.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = client.get(f"/jobs/{job_id}", headers=AUTH).get_json()
        if record.get("status") in job_schema.TERMINAL_STATUSES:
            return record
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never finished: {record}")


def _job(job_id: str, **overrides) -> dict:
    record = {
        "job_id": job_id,
        "source": "agent",
        "actor": "someone",
        "playbook": "reads/ping_localhost",
        "catalogue_commit": "unpinned",
        "variables": {},
        "limit_pattern": "",
        "check_mode": True,
        "status": "running",
        "exit_code": None,
        "started_at": time.time(),
        "finished_at": None,
        "events": [],
        "output": "",
        "error": "",
        "change_id": "",
    }
    record.update(overrides)
    return record


@pytest.fixture
def store(tmp_path):
    return executor_store.JobStore(str(tmp_path / "jobs.sqlite"))


# --- the property the whole change exists for --------------------------------

def test_a_job_survives_a_restart(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    first = executor_store.JobStore(path)
    first.record(_job("abc", status="succeeded", exit_code=0))
    first.close()

    reopened = executor_store.JobStore(path)
    found = reopened.get("abc")
    assert found is not None, "a restart must not lose what ran"
    assert found["status"] == "succeeded"
    assert found["exit_code"] == 0


def test_structured_events_come_back_as_structure_not_text(store):
    events = [{"task": "configure vlan", "commands": ["vlan 40", "name finance"]}]
    store.record(_job("abc", events=events))
    assert store.get("abc")["events"] == events


def test_check_mode_comes_back_as_a_boolean(store):
    """SQLite has no boolean, and a job that reports check_mode as 1 reads as a
    job that ran for real to anyone skimming the history."""
    store.record(_job("abc", check_mode=False))
    assert store.get("abc")["check_mode"] is False


# --- listing -----------------------------------------------------------------

def test_history_is_newest_first(store):
    now = time.time()
    store.record(_job("older", started_at=now - 100))
    store.record(_job("newer", started_at=now))
    assert [row["job_id"] for row in store.list()] == ["newer", "older"]


def test_filters_are_exact_matches_not_patterns(store):
    """A LIKE here would be a small convenience and a way to ask the store about
    rows the caller was never offered."""
    store.record(_job("a", playbook="reads/interfaces"))
    store.record(_job("b", playbook="reads/device_facts"))
    assert [row["job_id"] for row in store.list(playbook="reads/interfaces")] == ["a"]
    assert store.list(playbook="reads/%") == []


def test_a_listing_omits_the_bulky_detail(store):
    store.record(_job("a", events=[{"commands": ["x"]}], output="pages and pages"))
    (row,) = executor_store.rows_to_public(store.list())
    assert "events" not in row and "output" not in row
    assert row["event_count"] == 1


def test_the_page_size_cannot_be_used_to_ask_for_everything(store):
    for index in range(20):
        store.record(_job(f"job-{index}"))
    assert len(store.list(limit=10_000)) == 20  # capped, not refused
    assert len(store.list(limit=5)) == 5


# --- retention ---------------------------------------------------------------

def test_retention_drops_what_is_older_than_the_window(store, monkeypatch):
    monkeypatch.setenv(executor_store.ENV_RETENTION_DAYS, "1")
    store.record(_job("stale", started_at=time.time() - (3 * 86400)))
    store.record(_job("fresh"))
    store.prune()
    assert store.get("stale") is None
    assert store.get("fresh") is not None


def test_retention_caps_the_number_of_rows(store, monkeypatch):
    """Both limits, not either: an idle month must not keep a job forever, and a
    busy afternoon must not fill the volume."""
    monkeypatch.setenv(executor_store.ENV_RETENTION_MAX, "3")
    now = time.time()
    for index in range(6):
        store.record(_job(f"job-{index}", started_at=now + index))
    assert store.count() == 3
    assert [row["job_id"] for row in store.list()] == ["job-5", "job-4", "job-3"]


# --- updating ----------------------------------------------------------------

def test_an_update_ignores_a_column_it_does_not_own(store):
    """A job thread calls update() in a finally. A typo there must not be the
    thing that loses the record of a change that reached a device."""
    store.record(_job("abc"))
    store.update("abc", status="succeeded", playbook="something/else", nonsense=1)
    found = store.get("abc")
    assert found["status"] == "succeeded"
    assert found["playbook"] == "reads/ping_localhost"


def test_an_update_with_nothing_recognisable_is_a_no_op(store):
    store.record(_job("abc"))
    store.update("abc", nonsense=1)
    assert store.get("abc")["status"] == "running"


# --- the labels the executor stores but never acts on ------------------------

def test_record_only_fields_never_reach_ansible(client, monkeypatch):
    """`actor` and `change_id` are labels for a history view. If either could
    reach extravars it would be a caller-controlled variable inside a play, which
    is the whole class of thing the typed catalogue exists to prevent."""
    seen: dict = {}

    def capture(entry, variables, limit, check_mode):
        seen["variables"] = variables
        return {"status": "succeeded", "exit_code": 0, "output": "", "events": []}

    monkeypatch.setattr(executor_app, "_run_playbook", capture)
    response = client.post(
        "/jobs",
        json={
            "playbook_name": "reads/ping_localhost",
            "commit": "unpinned",
            "variables": {},
            "limit": "",
            "check_mode": True,
            "actor": "alice",
            "change_id": "CHG-1",
        },
        headers=AUTH,
    )
    assert response.status_code == 202
    _settle(client, response.get_json()["job_id"])
    assert seen["variables"] == {}, "a label must not become a play variable"


def test_the_labels_are_recorded_even_though_they_are_not_run(client, monkeypatch):
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    response = client.post(
        "/jobs",
        json={
            "playbook_name": "reads/ping_localhost",
            "commit": "unpinned",
            "variables": {},
            "limit": "",
            "check_mode": True,
            "actor": "alice",
            "change_id": "CHG-1",
        },
        headers=AUTH,
    )
    record = executor_store.store().get(response.get_json()["job_id"])
    assert record["actor"] == "alice"
    assert record["change_id"] == "CHG-1"
    assert record["source"] == "agent", "everything through this API came from the application"


# --- the listing endpoint ----------------------------------------------------

def _submit(client, monkeypatch, **body):
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "x" * 100, "events": []},
    )
    payload = {
        "playbook_name": "reads/ping_localhost",
        "commit": "unpinned",
        "variables": {},
        "limit": "",
        "check_mode": True,
    }
    payload.update(body)
    return client.post("/jobs", json=payload, headers=AUTH)


def test_the_listing_needs_a_token(client):
    assert client.get("/jobs").status_code == 401


def test_the_listing_reports_what_ran(client, monkeypatch):
    _submit(client, monkeypatch)
    _submit(client, monkeypatch)
    body = client.get("/jobs", headers=AUTH).get_json()
    assert body["total"] == 2
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["playbook_name"] == "reads/ping_localhost"


def test_the_listing_does_not_carry_the_bulky_detail(client, monkeypatch):
    """A page of two hundred jobs does not need two hundred stdout tails. A
    caller that wants one asks for that job."""
    _submit(client, monkeypatch)
    (job,) = client.get("/jobs", headers=AUTH).get_json()["jobs"]
    assert "output" not in job and "events" not in job
    # The job runs on its own thread: on a busy machine the listing is read before it has written its output.
    for _ in range(500):
        if client.get(f"/jobs/{job['job_id']}", headers=AUTH).get_json()["output"]:
            break
        time.sleep(0.01)
    assert client.get(f"/jobs/{job['job_id']}", headers=AUTH).get_json()["output"]


def test_a_filter_that_matches_nothing_is_empty_not_everything(client, monkeypatch):
    """Fail closed here too: an unrecognised filter value must narrow to nothing
    rather than be dropped and quietly widen the answer."""
    _submit(client, monkeypatch)
    body = client.get("/jobs?status=nonsense", headers=AUTH).get_json()
    assert body["jobs"] == [] and body["total"] == 0


def test_the_listing_uses_the_api_field_names_not_the_column_names(client, monkeypatch):
    """`limit` and `commit` are SQL keywords, so the store renames them. That is
    a storage detail and must not reach a caller."""
    _submit(client, monkeypatch)
    (job,) = client.get("/jobs", headers=AUTH).get_json()["jobs"]
    assert "playbook_name" in job and "commit" in job and "limit" in job
    assert "limit_pattern" not in job and "catalogue_commit" not in job


# --- what health says about the image ----------------------------------------

def test_health_reports_the_image_without_a_token(client):
    body = client.get("/healthz").get_json()
    assert "collections" in body and isinstance(body["collections"], dict)
    assert "ansible_version" in body
    assert body["uptime_seconds"] >= 0


def test_health_counts_jobs_but_does_not_list_them(client, monkeypatch):
    """/healthz has no token. How many jobs ran is not a description of the
    estate; which devices they touched is."""
    _submit(client, monkeypatch, limit="core-switch-01")
    history = client.get("/healthz").get_json()["history"]
    assert history["jobs"] == 1
    assert set(history) == {"jobs", "by_status", "store_bytes"}
    assert "core-switch-01" not in client.get("/healthz").get_data(as_text=True)


def test_health_still_leaks_no_secret(client, monkeypatch):
    _submit(client, monkeypatch)
    assert TOKEN not in client.get("/healthz").get_data(as_text=True)


def test_a_column_name_the_store_does_not_have_is_refused(store):
    """The store's names and the API's differ on purpose -- `limit` and `commit`
    are SQL keywords -- so passing the API's name is an easy mistake and one that
    silently loses the value. It has already happened once, in the break-glass
    console, where it showed as a blank column and nothing reported it."""
    with pytest.raises(ValueError, match="Unknown job columns"):
        store.record({**_job("abc"), "limit": "core-sw-1"})
