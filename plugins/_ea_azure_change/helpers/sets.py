"""An Azure change set: one record, one document, one CR or SR number, many ordered template steps (ADR 0073).

Everything specific to a set lives here; `records.py` and the tools only branch into it. A step is exactly what a single change
is (one template, one group, typed values, its own plan digest), planned by the executor in one `plan-set` call and applied one
at a time through the same `/v1/apply` a single change uses, under the id `<set id>.<nn>`. The approved artefact is the **set
digest** (`gate.set_digest`, made in the executor), stored as the record's `plan_digest` so the hash covers it unchanged.

The set **stops at the first step that does not apply** (a different plan digest, a failure, a failed verify) and nothing is
rolled back: the product never deletes. The rest is drafted again as a new set (`continues`) and approved again.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Callable

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLIED, APPLYING, AWAITING_APPROVAL, FAILED, FROM_REVIEWED_CLI, SUPERSEDED, VERIFY_FAILED, ChangeRecord
from plugins._ea_azure_change.helpers import drift, executor_client, records, tier
from plugins._ea_inventory.helpers import redact

try:
    from cloud_executor.gate import SET_MAX_STEPS, set_digest
except ImportError:  # pragma: no cover - not in this image: no digest can match, so no set is drafted or applied
    SET_MAX_STEPS = 12

    def set_digest(subscription: str, steps: list[dict[str, Any]]) -> str:
        return ""

log = logging.getLogger(__name__)

PLAYBOOK = "set"
STEP_KEYS = {"template", "resource_group", "variables"}
#: The executor admits six requests a minute. A step it turns away (HTTP 429) did not start, so it is tried again after this wait,
#: at most `RATE_TRIES` times in all (a minute and a half), and only then does the call end with the step still to run.
RATE_WAIT_SECONDS = 15
RATE_TRIES = 6


def is_set(record: Any) -> bool:
    return record.kind == "azure_change" and record.playbook_name == PLAYBOOK


def subscription_of(record: ChangeRecord) -> str:
    return records.workspace_of(record.limit)["subscription"]


def limit_of(subscription: str, steps: list[dict[str, Any]]) -> str:
    """`<subscription>/<group>,<group>...`: the distinct groups, in step order."""
    return f"{subscription}/{','.join(dict.fromkeys(s['resource_group'] for s in steps))}"


def commands_of(steps: list[dict[str, Any]]) -> list[str]:
    """One line per step, the lines the record hashes and the card shows: `1. vnet in rg-hub: name = "vnet-hub", ...`."""
    return [f"{s['n']}. {s['template']} in {s['resource_group']}: " + ", ".join(records.describe_values(s["variables"])) for s in steps]


def plan_lines(record: ChangeRecord) -> list[str]:
    """Every step's plan lines, each led by its step number: what the agent says and `records.differences` compares."""
    return [f"[{s['n']}] {line}" for s in record.steps for line in (s.get("summary") or {}).get("lines") or []]


def steps_match(record: ChangeRecord) -> bool:
    """The steps are the ones the set digest was made from: the digest recomputed from them equals `plan_digest`, and the hashed
    `commands` and `limit` still read the same as the steps' values and groups (the values are not in the digest, the commands are
    in the hash). A broken or reordered list is a mismatch, never an error."""
    try:
        steps, sub = record.steps, subscription_of(record)
        return (2 <= len(steps) <= SET_MAX_STEPS and [s["n"] for s in steps] == list(range(1, len(steps) + 1))
                and bool(record.plan_digest) and record.plan_digest == set_digest(sub, steps)
                and record.commands == commands_of(steps) and record.limit == limit_of(sub, steps))
    except (KeyError, TypeError, AttributeError):
        return False


def remaining(record: ChangeRecord) -> list[dict[str, Any]]:
    """The steps from the one the set stopped at to the last, as a draft takes them (`continues`)."""
    k = int((record.preflight.get("set") or {}).get("stopped_at") or 0)
    return [{key: s[key] for key in sorted(STEP_KEYS)} for s in record.steps if s["n"] >= k > 0]


def steps_problem(subscription: str, steps: Any) -> str | None:
    """Why these steps may not be drafted as a set, or None: the count, the shape of each step (a `code` key is free code, which is
    never a step), the template, the scope. The executor checks all of it again."""
    if not isinstance(steps, list) or not steps:
        return f"`steps` is a list of 2 to {SET_MAX_STEPS} steps, each with `template`, `resource_group` and `variables`."
    if len(steps) == 1:
        return f"A change set has 2 to {SET_MAX_STEPS} steps. One step is a single change: draft it with action=draft."
    if len(steps) > SET_MAX_STEPS:
        return (f"A change set has 2 to {SET_MAX_STEPS} steps and this has {len(steps)}. Tell the engineer it is too big for one set and ask "
                "how they want it divided; do not split it yourself.")
    catalogue = records.templates()
    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict) or set(step) != STEP_KEYS or not isinstance(step["variables"], dict):
            return (f"Step {n}: a step is `template`, `resource_group` and `variables` (an object), and nothing else. "
                    "Free code is never a step.")
        if catalogue and step["template"] not in catalogue:
            return f"Step {n}: there is no template named `{step['template']}`. Templates that exist:\n{records.catalogue_text()}"
        if problem := records.scope_problem(subscription, str(step["resource_group"])):
            return f"Step {n}: {problem}"
    return None


# --- from a plan to a record ----------------------------------------------------------------------------------


def ask_plan_set(agent: Any, *, subscription: str, steps: list[dict[str, Any]], change_id: str, requester: str) -> tuple[dict[str, Any], str]:
    """The executor's plan of the whole set and the template catalogue commit it reports. Network only: run it off the event loop."""
    for group in dict.fromkeys(s["resource_group"] for s in steps):  # the executor does not know the engineer's unaccepted drift
        if problem := drift.draft_problem(subscription, group):
            raise records.PlanRefused(problem)
    commit = str(executor_client.health(agent).get("templates_commit") or "")
    if not commit:
        raise records.PlanRefused("The cloud executor reports no template catalogue commit, so nothing can be pinned to a reviewed version.")
    asked = [{key: s[key] for key in sorted(STEP_KEYS)} for s in steps]
    return executor_client.plan_set(agent, subscription=subscription, steps=asked, change_id=change_id, actor=requester), commit


def check_answer(answer: dict[str, Any], steps: list[dict[str, Any]], subscription: str, rail: dict[str, Any]) -> None:
    """Defence in depth on the executor's answer, step by step and for the sums, as `records.check_plan_answer` is for one change."""
    if not answer.get("ok"):
        where = f" at step {answer['step']}" if answer.get("step") else ""
        raise records.PlanRefused(f"The change set was refused{where} at the '{answer.get('stage') or 'plan'}' stage: {answer.get('refusal') or 'no reason given'}")
    got = answer.get("steps") or []
    if [g.get("n") for g in got] != list(range(1, len(steps) + 1)):
        raise records.PlanRefused("The executor's answer does not hold the steps that were asked for, so it was refused.")
    for g in got:
        try:
            records.check_plan_answer({"ok": True, **g})
        except records.PlanRefused as error:
            raise records.PlanRefused(f"Step {g['n']}: {error}") from error
        if records.nothing_to_change(g):
            raise records.NothingToChange(f"Step {g['n']} would change nothing: Azure already matches.")
    ordered = [{"template": s["template"], "resource_group": s["resource_group"], "plan_digest": g["plan_digest"]} for s, g in zip(steps, got)]
    if not answer.get("set_digest") or answer["set_digest"] != set_digest(subscription, ordered):
        raise records.PlanRefused("The set digest is not the one made from the steps' digests, so it was refused.")
    failed = [c for c in answer.get("checks") or [] if not c.get("ok") and c.get("level") != "warn"]
    if failed:
        raise records.PlanRefused("A check failed: " + "; ".join(f"{c.get('label')}: {c.get('detail') or 'failed'}" for c in failed))
    ceiling = float(rail["usd_month_per_change"])
    for monthly in ((answer.get("cost") or {}).get("monthly_usd"), sum(float((g.get("cost") or {}).get("monthly_usd") or 0) for g in got)):
        if monthly is not None and float(monthly) > ceiling:
            raise records.PlanRefused(f"About {monthly} USD a month for the whole set is over the {ceiling:g} USD ceiling for one change.")
    count = sum(int((g.get("summary") or {}).get(k) or 0) for g in got for k in ("add", "change", "import"))
    if count > int(rail["max_resources"]):
        raise records.PlanRefused(f"{count} resources in the set is more than the limit of {rail['max_resources']}.")


def _title(step: dict[str, Any]) -> str:
    name = step["variables"].get("name")
    return records._one_line(f"{step['template']}{' ' + name if isinstance(name, str) and name else ''} in {step['resource_group']}", 90)  # noqa: SLF001


def build(agent: Any, answer: dict[str, Any], commit: str, *, change_id: str, requester: str, subscription: str, steps: list[dict[str, Any]],
          purpose: str, continues: str = "", planned_note: str = "") -> ChangeRecord:
    """Accept the executor's answer (or raise `PlanRefused`) and make the set's record, not saved."""
    rail = records.rails(agent)
    check_answer(answer, steps, subscription, rail)
    catalogue = records.templates()
    rows, summed, lines_new = [], {"add": 0, "change": 0, "import": 0}, set()
    for ask, got in zip(steps, answer["steps"]):
        entry = catalogue.get(ask["template"]) or {}
        summary = got.get("summary") or {}
        for key in summed:
            summed[key] += int(summary.get(key) or 0)
        if any(r.get("type") == "azurerm_resource_group" and r.get("action") == "create" for r in summary.get("resources") or []):
            lines_new.add(ask["resource_group"])
        step = {"n": len(rows) + 1, "template": ask["template"], "resource_group": ask["resource_group"], "variables": ask["variables"],
                "plan_digest": str(got["plan_digest"]), "risk": str(entry.get("risk") or ""), "summary": summary, "cost": got.get("cost") or {},
                "checks": [{"label": str(c.get("label")), "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")} for c in got.get("checks") or []],
                "backout": str(entry.get("backout") or ""), "status": "planned", "job_id": "", "started_at": None, "ended_at": None,
                "verified": None, "changed_ids": [], "error": ""}
        rows.append({**step, "title": _title(step)})
    cost = answer.get("cost") or {}
    level, reasons = tier.decide_set({s["template"]: catalogue.get(s["template"]) for s in rows}, {"summary": summed, "cost": cost}, rail)
    monthly = cost.get("monthly_usd")
    checks = [{"label": str(c.get("label")), "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")} for c in answer.get("checks") or []]
    checks.append({"label": "Nothing is deleted, stopped or restarted", "ok": True, "detail": "every change in every step is a create or an update"})
    checks.append({"label": "Cost of the set within the ceiling for one change", "ok": True,
                   "detail": f"{'unknown' if monthly is None else monthly} USD a month against {rail['usd_month_per_change']}"})
    if planned_note:
        checks.append({"label": planned_note, "ok": True, "detail": ""})
    advice = records.advice_for(purpose, rows[0]["variables"], subscription)
    checks += [{"label": f"Advice: {f.get('check')}", "ok": True, "level": "warn", "detail": str(f.get("summary") or "")} for f in advice]
    now = time.time()
    regions = sorted({str(s["variables"]["region"]).replace(" ", "").lower() for s in rows if s["variables"].get("region")})
    groups = list(dict.fromkeys(s["resource_group"] for s in rows))
    return ChangeRecord(
        id=change_id, created_at=now, requested_by=requester, status=AWAITING_APPROVAL, kind="azure_change", playbook_name=PLAYBOOK, commit=commit,
        variables={}, limit=limit_of(subscription, rows), commands=commands_of(rows), diff=[], render_method=FROM_REVIEWED_CLI, rendered_at=now,
        summary=records._one_line(purpose) or f"Change set of {len(rows)} steps in {', '.join(groups)}",  # noqa: SLF001
        rollback_commands=[f"Step {s['n']}: {line}" for s in reversed(rows) for line in s["backout"].splitlines() if line.strip()],
        expires_at=now + records.valid_days(agent) * records.DAY, plan_digest=str(answer["set_digest"]), tier=level, confirm="", steps=rows,
        preflight={"purpose": records._one_line(purpose, 1000),  # noqa: SLF001
                   "scope": {"subscription": subscription, "resource_groups": [{"name": g, "new": g in lines_new} for g in groups], "regions": regions},
                   "summary": summed, "cost": cost, "checks": checks, "advice": advice, "risk_reasons": reasons,
                   "usd_month_per_change": rail["usd_month_per_change"], "plan_digest": str(answer["set_digest"]), "continues": continues,
                   "drift": [line for g in answer["steps"] for line in g.get("drift") or []],
                   "set": {"steps": len(rows), "applied": 0, "stopped_at": 0}})


def draft(agent: Any, *, subscription: str, steps: list[dict[str, Any]], purpose: str, requester: str, continues: str = "") -> tuple[ChangeRecord, list[str]]:
    """Plan the steps and make the record, not saved; with `continues`, steps that now change nothing are dropped (their titles are
    returned). Network only: run it off the event loop."""
    steps, dropped = list(steps), []
    while True:
        change_id = records.new_id()
        if len(steps) < 2:
            raise records.PlanRefused(f"Only one step is left ({_title(steps[0])}): it is a single change, draft it with action=draft.")
        answer, commit = ask_plan_set(agent, subscription=subscription, steps=steps, change_id=change_id, requester=requester)
        if continues and not answer.get("ok") and answer.get("step") and "changes nothing" in str(answer.get("refusal")):
            dropped.append(_title(steps.pop(int(answer["step"]) - 1)))
            continue
        return build(agent, answer, commit, change_id=change_id, requester=requester, subscription=subscription, steps=steps, purpose=purpose,
                     continues=continues), dropped


# --- the apply ------------------------------------------------------------------------------------------------------


def _audit_step(record: ChangeRecord, step: dict[str, Any]) -> None:
    """`azure.change.step`: which step, its executor id and digest and what became of it. No plan, value or document."""
    if change_store.audit is not None:
        change_store.audit.emit("azure.change.step", change_id=record.id, step=step["n"], step_change_id=f"{record.id}.{step['n']:02d}",
                                template=step["template"], workspace=f"{subscription_of(record)}/{step['resource_group']}",
                                plan_digest=step["plan_digest"], status=step["status"], job_id=step["job_id"], reference=record.reference,
                                requested_by=record.requested_by, approved_by=record.approved_by)


def _record_step(record: ChangeRecord, step: dict[str, Any], job: dict[str, Any]) -> None:
    """Write a terminal job's answer into its step, list what it made for drift and adoption, and audit it."""
    state = str(job.get("status") or "")
    changed = [str(x) for x in job.get("changed_ids") or []]
    step.update(status=state, changed_ids=changed, verified=job.get("verified") if state in ("applied", "verify_failed") else None,
                started_at=records._epoch(job.get("started_at")) or step["started_at"], ended_at=records._epoch(job.get("ended_at")) or time.time(),  # noqa: SLF001
                error=redact.redact_text(str(job.get("error") or ""))[0][:500])
    if changed:
        records.record_managed(dataclasses.replace(record, limit=f"{subscription_of(record)}/{step['resource_group']}"), changed)
    _audit_step(record, step)


def _stopped_text(step: dict[str, Any]) -> str:
    return step["error"] or {"verify_failed": "it was applied, but the verify did not find the intended state",
                             "superseded": "the plan changed since it was approved, so nothing of this step ran"}.get(step["status"], "it failed")


def _finish(record: ChangeRecord, actor: str) -> str:
    """The set has ended (every step applied, or one did not): its status, the document and the message for the agent."""
    steps, total = record.steps, len(record.steps)
    stop = next((s for s in steps if s["status"] != "applied"), None)
    for s in steps:
        if s["status"] == "planned" and stop is not None and s["n"] > stop["n"]:
            s["status"] = "not_run"
    ran = [s for s in steps if s["job_id"] and s["status"] != "superseded"]
    done = [s for s in steps if s["status"] == "applied"]
    if stop is None:
        record.status = APPLIED
        values = [s["verified"] for s in steps if s["verified"] is not None]
        record.verified = None if not values else all(values)
        record.error = ""
    else:
        k = stop["n"]
        record.status = VERIFY_FAILED if stop["status"] == "verify_failed" else SUPERSEDED if stop["status"] == "superseded" and k == 1 else FAILED
        record.verified = False if stop["status"] == "verify_failed" else None
        kept = "Nothing was applied." if k == 1 else "Step 1 is applied and stays in Azure." if k == 2 else f"Steps 1 to {k - 1} are applied and stay in Azure."
        record.error = f"Stopped at step {k} of {total}: {_stopped_text(stop).rstrip('.')}. {kept}"[:500]
    record.preflight["set"] = {"steps": total, "applied": len(done), "stopped_at": stop["n"] if stop else 0}
    record.job_id = steps[max(0, (stop["n"] if stop else total) - 1)]["job_id"] or record.job_id
    if ran:
        record.applied_at = max(float(s["ended_at"] or 0) for s in ran) or time.time()
    pulled, pulled_at = records.refresh_inventory(actor) if ran else ("not needed", None)
    change_store.save(record)  # the status line is audited here, before the document is written
    note = ""
    if ran:
        try:
            records.write_documents(record, "applied", {"started_at": min(float(s["started_at"] or 0) for s in ran) or None, "ended_at": record.applied_at,
                                                        "verified": record.verified, "error": record.error,
                                                        "changed_ids": [i for s in steps for i in s["changed_ids"]], "inventory_pulled_at": pulled_at})
            change_store.save(record)
            records.audit_document(record, "applied")
        except Exception as error:  # noqa: BLE001 - the apply is done; say that the document was not
            log.error("azure change: applied document not written (%s)", type(error).__name__)
            note = "\nThe as-applied document could not be written; the record and the audit hold the result."
    lines = []
    for s in steps:
        made = f" Made: {', '.join(s['changed_ids'][:5])}{' ...' if len(s['changed_ids']) > 5 else ''}." if s["changed_ids"] else ""
        say = {"applied": "applied and verified", "verify_failed": "applied, but the verify did not find the intended state",
               "failed": f"stopped: {_stopped_text(s)}", "superseded": f"stopped: {_stopped_text(s)}", "not_run": "not run"}.get(s["status"], s["status"])
        lines.append(f"  {s['n']}. {s['title']}: {say}.{made}")
    head = (f"Change set `{record.id}` applied: all {total} steps, in order." if stop is None else
            f"Change set `{record.id}` stopped at step {stop['n']} of {total}. {record.error}")
    tail = ""
    if stop is not None:
        tail = ("\nNothing is rolled back: the product never deletes or stops anything. The back-out steps for what was applied are in the as-applied "
                "document, part 9, for the engineer to follow by hand."
                + f" To go on, draft the rest with azure_change action=draft_set and continues=`{record.id}`: it plans steps {stop['n']} to {total} again "
                "and needs a new document, a new approval and a CR or SR number (the same number may be typed again).")
    return f"{head}\n" + "\n".join(lines) + f"{tail}\nInventory pull: {pulled}.{note}"


def _wait(agent: Any, record: ChangeRecord, step: dict[str, Any]) -> str | None:
    """Wait for the step's job; record it and return None when it ended, else the sentence for a job still running."""
    job = executor_client.wait(agent, step["job_id"], executor_client.config(agent)["apply_wait_seconds"])
    if job.get("status") not in executor_client.TERMINAL:
        return f"Change set `{record.id}` is still applying step {step['n']} of {len(record.steps)} (job {step['job_id']}). Tell the user, and call azure_apply again later to settle it and go on."
    _record_step(record, step, job)
    return None


def _start(agent: Any, record: ChangeRecord, step: dict[str, Any], actor: str, say: Callable[[str], None]) -> str:
    """Start one step and return its job id. A 429 means the executor turned it away before doing anything: wait, say so, try the
    same step again, a bounded number of times."""
    for attempt in range(1, RATE_TRIES + 1):
        try:
            return executor_client.apply(
                agent, source={"template": step["template"], "variables": step["variables"]},
                workspace={"subscription": subscription_of(record), "resource_group": step["resource_group"]},
                plan_digest=step["plan_digest"], reference=record.reference, change_id=f"{record.id}.{step['n']:02d}", actor=actor)
        except executor_client.ExecutorBusy:
            if attempt == RATE_TRIES:
                raise
            say(f"Step {step['n']} of {len(record.steps)}: the executor takes six changes a minute and turned this one away. Waiting "
                f"{RATE_WAIT_SECONDS} seconds, then trying step {step['n']} again ({attempt} of {RATE_TRIES - 1}).")
            time.sleep(RATE_WAIT_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


def run(agent: Any, record: ChangeRecord, actor: str, progress: Callable[[str], None] | None = None) -> str:
    """Apply an approved set (or go on with one that is applying), step by step, and return the message for the agent.

    Before each step the gate's conditions run again; the executor plans the step again and applies it only if its digest is the
    approved one. The first step that is not `applied` stops the set: later steps never reach the executor. Blocking: run it off the
    event loop. `progress` is told about a wait."""
    from plugins._ea_azure_change.helpers import conditions  # here, not at the top: conditions imports this module

    say = progress or (lambda text: None)
    with records.in_flight(record.id) as mine:
        if not mine:
            return f"Change set `{record.id}` is already being applied by another call. Check it later with azure_change status."
        for step in record.steps:
            if step["status"] == "applied":
                continue
            if step["status"] == "applying":  # a wait ran out earlier: settle that step and go on
                if running := _wait(agent, record, step):
                    change_store.save(record)
                    return running
                change_store.save(record)
            elif step["status"] == "planned":
                if refusal := conditions.first_failure(record, actor):
                    if step["n"] == 1:  # nothing ran: the record stays as it was, as a single change does
                        return records.pass_on(refusal)
                    step.update(status="failed", error=refusal[:500], ended_at=time.time())
                    break
                if not change_store.take(record, step=step["n"]):  # the register lets each step be taken once, in order
                    return records.pass_on(f"Change set {record.id} has no approval left for step {step['n']}: it already ran, or is running. Nothing ran.")
                try:
                    step["job_id"] = _start(agent, record, step, actor, say)
                except executor_client.ExecutorBusy as busy:  # nothing started: the step stays planned
                    change_store.give_back(record, step=step["n"])  # nothing of it ran: at step 1 the set is as it was
                    return (f"Change set `{record.id}`: the executor is still turning step {step['n']} away ({busy}). Nothing of it ran. "
                            "Tell the user, and call azure_apply again in a minute to go on.")
                except executor_client.ExecutorError as error:  # refused before a job existed: nothing of this step ran
                    step.update(status="failed", error=str(error)[:500], ended_at=time.time())
                    break
                step.update(status="applying", started_at=time.time())
                record.job_id = step["job_id"]
                change_store.save(record)
                try:
                    running = _wait(agent, record, step)
                except executor_client.ExecutorError as error:
                    return f"Change set `{record.id}` started step {step['n']} (job {step['job_id']}), but its state could not be read now: {error}. Tell the user; check it later with azure_change status."
                change_store.save(record)
                if running:
                    return running
            if step["status"] != "applied":
                break
        return _finish(record, actor)


def settle(agent: Any, record: ChangeRecord, actor: str) -> str | None:
    """For `azure_change status` on an applying set: if the step that was running has ended, record it. When that step did not
    apply, the set has stopped: finish it and return the message; when it did, say what is left. None while it still runs."""
    step = next((s for s in record.steps if s["status"] == "applying"), None)
    if record.status != APPLYING or step is None:
        return None
    job = executor_client.job(agent, step["job_id"])
    if job.get("status") not in executor_client.TERMINAL:
        return None
    _record_step(record, step, job)
    if step["status"] != "applied" or all(s["status"] == "applied" for s in record.steps):
        return _finish(record, actor)
    change_store.save(record)
    return f"Step {step['n']} of {len(record.steps)} applied. Steps {step['n'] + 1} to {len(record.steps)} have not started: call azure_apply to go on."
