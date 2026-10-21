"""Who runs now, who waits, and who is turned away.

The executor used to answer 429 to anything arriving while a job ran. That was
right while the agent was the only caller: it retries, and network change is not
a throughput problem. It stops being right once a person is holding the other
end -- an engineer told "try again shortly" twice has learned the button is
unreliable.

So the refusals moved rather than went away. These tests are about which ones
survived and why: a queue deep enough to absorb two people pressing things, and
a refusal kept for the cases where waiting would be the wrong answer.
"""

from __future__ import annotations

import threading
import time

import pytest

from executor import app as executor_app
from executor import scheduling
from executor import store as executor_store
from plugins._ea_ansible.helpers import job_schema
from plugins._ea_ansible.tests.conftest import AUTH, drain

JOB = {
    "playbook_name": "reads/ping_localhost",
    "commit": "unpinned",
    "variables": {},
    "limit": "",
    "check_mode": True,
}


@pytest.fixture
def blocking(monkeypatch):
    """A job that runs until the test lets it finish."""
    release = threading.Event()
    started = threading.Semaphore(0)

    def run(*_args, **_kwargs):
        started.release()
        release.wait(timeout=5)
        return {"status": "succeeded", "exit_code": 0, "output": "", "events": []}

    monkeypatch.setattr(executor_app, "_run_playbook", run)
    try:
        yield type("Blocking", (), {"release": release, "started": started})
    finally:
        release.set()
        drain()


def _status(client, job_id: str) -> str:
    return client.get(f"/jobs/{job_id}", headers=AUTH).get_json()["status"]


def _wait_for(predicate, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never held")


# --- queueing ----------------------------------------------------------------

def test_a_second_job_waits_its_turn_rather_than_being_refused(client, blocking):
    first = client.post("/jobs", json=JOB, headers=AUTH).get_json()
    assert blocking.started.acquire(timeout=5)

    second = client.post("/jobs", json=JOB, headers=AUTH)
    assert second.status_code == 202, "a person pressing a button twice is not an error"
    assert second.get_json()["status"] == "queued"
    assert _status(client, first["job_id"]) == "running"


def test_a_queued_job_runs_when_the_one_ahead_of_it_finishes(client, blocking):
    client.post("/jobs", json=JOB, headers=AUTH)
    assert blocking.started.acquire(timeout=5)
    second = client.post("/jobs", json=JOB, headers=AUTH).get_json()
    assert second["status"] == "queued"

    blocking.release.set()
    _wait_for(lambda: _status(client, second["job_id"]) in job_schema.TERMINAL_STATUSES)


def test_queued_is_not_a_finished_state(client, blocking):
    """The client's wait loop used to break on "not running", which would have
    reported a job that had not started as one that had."""
    client.post("/jobs", json=JOB, headers=AUTH)
    assert blocking.started.acquire(timeout=5)
    queued = client.post("/jobs", json=JOB, headers=AUTH).get_json()
    assert queued["status"] not in job_schema.TERMINAL_STATUSES


# --- the refusals that survived ----------------------------------------------

def test_a_queue_deeper_than_the_cap_refuses_rather_than_growing(client, blocking, monkeypatch):
    monkeypatch.setenv(scheduling.ENV_MAX_QUEUED, "1")
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "100")
    client.post("/jobs", json=JOB, headers=AUTH)
    assert blocking.started.acquire(timeout=5)

    assert client.post("/jobs", json=JOB, headers=AUTH).status_code == 202
    refused = client.post("/jobs", json=JOB, headers=AUTH)
    assert refused.status_code == 429
    assert "already waiting" in refused.get_json()["error"]


def test_a_burst_faster_than_a_person_is_refused(client, monkeypatch):
    """The limit lives in the executor because a limit only the application
    enforces is not a limit: the console and the shell reach the same place."""
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "3")
    codes = [client.post("/jobs", json=JOB, headers=AUTH).status_code for _ in range(5)]
    assert codes.count(429) == 2, codes


def test_a_refusal_is_recorded_rather_than_leaving_no_trace(client, monkeypatch):
    """"Why did nothing run" is a question about the jobs that did not."""
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "1")
    client.post("/jobs", json=JOB, headers=AUTH)
    client.post("/jobs", json=JOB, headers=AUTH)
    drain()
    statuses = [job["status"] for job in client.get("/jobs", headers=AUTH).get_json()["jobs"]]
    assert statuses.count("failed") == 1
    refused = [
        job for job in executor_store.store().list() if job["status"] == "failed"
    ][0]
    assert "last minute" in refused["error"]


# --- the failure that would wedge it -----------------------------------------

def test_a_job_that_raises_still_lets_the_next_one_start(client, monkeypatch):
    """A scheduler that leaked a slot per failure would stop the executor dead
    after max_concurrent_jobs bad jobs, and nothing would say why."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("ansible fell over")

    monkeypatch.setattr(executor_app, "_run_playbook", explode)
    first = client.post("/jobs", json=JOB, headers=AUTH).get_json()
    _wait_for(lambda: _status(client, first["job_id"]) == "failed")
    # The slot is given back a moment after the status is written: on a busy machine the status is seen first.
    _wait_for(lambda: executor_app._scheduler.depth() == {"running": 0, "queued": 0})

    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    second = client.post("/jobs", json=JOB, headers=AUTH).get_json()
    _wait_for(lambda: _status(client, second["job_id"]) == "succeeded")


# --- what the executor says it enforces --------------------------------------

def test_health_reports_the_limits_it_actually_enforces(client, monkeypatch):
    """A settings screen showing a number nobody enforces is worse than a screen
    showing nothing, so the number on the screen comes from here."""
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "7")
    body = client.get("/healthz").get_json()
    assert body["limits"]["max_jobs_per_minute"] == 7
    assert body["queue"] == {"running": 0, "queued": 0}


def test_a_nonsense_limit_falls_back_rather_than_disabling_the_cap(monkeypatch):
    """Zero or a typo must not read as "no limit". Failing open on a rate limit
    is the one failure mode that looks like it is working."""
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "not-a-number")
    assert scheduling.limits()["max_jobs_per_minute"] == scheduling.DEFAULT_MAX_PER_MINUTE
    monkeypatch.setenv(scheduling.ENV_MAX_PER_MINUTE, "0")
    assert scheduling.limits()["max_jobs_per_minute"] == scheduling.DEFAULT_MAX_PER_MINUTE


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the job thread ends on the error this test plants
def test_a_job_whose_record_cannot_be_written_still_gives_its_slot_back(client, monkeypatch):
    """The comment in `_run_and_record` says a failed job must let the next one start. A failed playbook did; a
    failed write of the job's own record (a full disk, a store that will not open) did not, because both writes
    were outside the `try`: the one slot stayed taken and every later job waited until the executor was restarted."""
    monkeypatch.setattr(executor_app, "_run_playbook",
                        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []})
    real_update = executor_store.JobStore.update
    broken = {"on": True}

    def update(self, job_id, **fields):
        if broken["on"]:
            raise OSError(28, "No space left on device")
        return real_update(self, job_id, **fields)

    monkeypatch.setattr(executor_store.JobStore, "update", update)
    assert client.post("/jobs", json=JOB, headers=AUTH).status_code == 202
    _wait_for(lambda: not executor_app._scheduler.busy())

    broken["on"] = False
    second = client.post("/jobs", json=JOB, headers=AUTH).get_json()["job_id"]
    _wait_for(lambda: _status(client, second) == "succeeded")
