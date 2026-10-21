"""The minute loop that settles an Azure change whose apply was interrupted (the application restarted while an apply job ran): a
change left `applying` is settled by one tick as its owner; a job still running is left alone; an executor that cannot be
reached, a plugin that is off or a change a call in this process is waiting on changes nothing; a change set is settled at the
step that was running and never continued. Against the stand-in executor."""

from __future__ import annotations

import asyncio

import pytest

from helpers import plugins, user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPLIED, APPLYING, FAILED
from plugins._ea_azure_change.extensions.python.job_loop._65_azure_change_settle import SettleInterruptedAzureChanges
from plugins._ea_azure_change.helpers import executor_client, records, settling
from plugins._ea_azure_change.tests.test_azure_change_flow import approved, run, world  # noqa: F401 - `world` is a fixture
from plugins._ea_azure_change.tests.test_azure_change_set import approved_set, applied_calls, jobs_for
from plugins._ea_azure_change.tools.azure_apply import AzureApply

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

RUNNING = {"status": "running", "started_at": 1.0}


def interrupted(world, record, job_id="job1"):
    """The state a restart leaves: `applying`, the job started, nobody waiting."""
    record.status, record.job_id = APPLYING, job_id
    register.force(record)


def tick() -> int:
    """One loop tick, as the framework runs it: outside any request, so the owner's scope has to be pushed by the loop itself."""
    with user_scope.user_scope(None):
        return settling.settle_open()


def test_a_change_left_applying_is_settled_by_one_tick_as_a_normal_apply_is(world):
    record = approved(world)
    interrupted(world, record)
    assert tick() == 1
    done = change_store.load(record.id)
    assert done.status == APPLIED and done.verified is True and done.applied_at and {(d["version"], d["format"]) for d in done.documents} >= {("applied", "pdf"), ("applied", "docx")}
    assert "azure.change.applied" in [e for e, _ in world.events] and "azure.change.document_issued" in [e for e, _ in world.events]
    assert tick() == 0 and world.paths().count("/v1/jobs/job1") == 1  # settled once, never asked again


def test_a_job_still_running_is_left_alone_and_settled_by_a_later_tick(world):
    record = approved(world)
    interrupted(world, record)
    world.job, finished = RUNNING, world.job
    assert tick() == 0 and change_store.load(record.id).status == APPLYING
    world.job = finished
    assert tick() == 1 and change_store.load(record.id).status == APPLIED


def test_an_executor_that_does_not_answer_changes_nothing_and_raises_nothing(world, monkeypatch):
    record = approved(world)
    interrupted(world, record)
    monkeypatch.setattr(executor_client, "job", lambda agent, job_id: (_ for _ in ()).throw(executor_client.ExecutorUnreachable("down")))
    assert tick() == 0 and change_store.load(record.id).status == APPLYING
    asyncio.run(SettleInterruptedAzureChanges(agent=None).execute())  # the extension swallows what it must


def test_the_loop_does_nothing_when_the_plugin_is_off_or_the_executor_is_not_configured_and_leaves_calls_in_progress_alone(world, monkeypatch):
    record = approved(world)
    interrupted(world, record)
    with monkeypatch.context() as off:
        off.setattr(plugins, "get_toggle_state", lambda name: "disabled")
        assert tick() == 0
    with monkeypatch.context() as unconfigured:
        unconfigured.setattr(executor_client, "is_configured", lambda agent=None: False)
        assert tick() == 0
    with records.in_flight(record.id):  # a call in this process is waiting on it: it settles its own
        assert tick() == 0
    assert change_store.load(record.id).status == APPLYING and "/v1/jobs/job1" not in world.paths()


def test_only_a_few_changes_are_looked_at_per_tick_oldest_first(world):
    many = [approved(world) for _ in range(settling.PER_TICK + 2)]
    for record in many:
        interrupted(world, record)
    assert tick() == settling.PER_TICK
    left = [r for r in many if change_store.load(r.id).status == APPLYING]
    assert len(left) == 2 and tick() == 2


def test_a_change_that_applied_and_one_that_failed_while_the_application_was_down_are_each_settled_as_they_ended(world):
    ok, bad = approved(world), approved(world)
    interrupted(world, ok)
    interrupted(world, bad, "job2")
    world.job = {"status": "failed", "error": "quota", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}
    assert tick() == 2
    assert {change_store.load(ok.id).status, change_store.load(bad.id).status} == {FAILED}


def test_a_set_interrupted_at_a_step_that_failed_is_settled_there_and_reported_stopped_and_the_loop_never_continues_it(world):
    record = approved_set(world)
    jobs_for(world, record, {3: {"status": "failed", "error": "quota", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}})
    for step in record.steps[:2]:
        step.update(status="applied", job_id=f"job-{record.id}-{step['n']:02d}", verified=True, started_at=1.0, ended_at=2.0, changed_ids=["/x"])
    record.steps[2].update(status="applying", job_id=f"job-{record.id}-03", started_at=3.0)
    interrupted(world, record, f"job-{record.id}-03")
    assert tick() == 1
    done = change_store.load(record.id)
    assert done.status == FAILED and [s["status"] for s in done.steps] == ["applied", "applied", "failed", "not_run", "not_run", "not_run"]
    assert done.error.startswith("Stopped at step 3 of 6") and done.preflight["set"] == {"steps": 6, "applied": 2, "stopped_at": 3}
    assert applied_calls(world) == [] and any(d["version"] == "applied" for d in done.documents)  # nothing was started; the as-applied document was written
    assert tick() == 0


def test_a_set_interrupted_at_a_step_that_applied_stays_applying_with_the_next_steps_for_the_engineers_apply_to_go_on(world):
    record = approved_set(world)
    jobs_for(world, record)
    for step in record.steps[:2]:
        step.update(status="applied", job_id=f"job-{record.id}-{step['n']:02d}", verified=True, started_at=1.0, ended_at=2.0, changed_ids=["/x"])
    record.steps[2].update(status="applying", job_id=f"job-{record.id}-03", started_at=3.0)
    interrupted(world, record, f"job-{record.id}-03")
    assert tick() == 1
    mid = change_store.load(record.id)
    assert mid.status == APPLYING and [s["status"] for s in mid.steps] == ["applied"] * 3 + ["planned"] * 3 and applied_calls(world) == []
    assert tick() == 0  # nothing is waiting on a job: the loop does not look at it again, and does not continue it
    message = run(AzureApply, change_id=record.id)  # the engineer's apply goes on under the same approval
    assert change_store.load(record.id).status == APPLIED and "applied: all 6 steps" in message
    assert [b["change_id"] for b in applied_calls(world)] == [f"{record.id}.{n:02d}" for n in (4, 5, 6)]


def test_a_set_whose_apply_call_is_still_waiting_in_this_process_is_not_settled_under_it(world):
    record = approved_set(world)
    jobs_for(world, record)
    record.steps[0].update(status="applying", job_id=f"job-{record.id}-01", started_at=3.0)
    interrupted(world, record, f"job-{record.id}-01")
    with records.in_flight(record.id):
        assert tick() == 0
    assert change_store.load(record.id).steps[0]["status"] == "applying"
