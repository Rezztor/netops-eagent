"""The drift report: what changed in Azure outside the product, shown and accepted, never put back (ADR 0072).

**Drift** is a change line, in an inventory pull, on a resource the product made (`cloud_managed`) that no apply explains
(`_ea_azure/helpers/store.drifted`, computed from stored rows). The agent lists it (`azure_change drift`) and can ask for it to be
**accepted** (`drift_accept`): the executor plans a refresh-only plan for the state that holds the resource (it only reads), the
change plugin saves an approval record (`kind` `azure_change`, playbook `drift_accept`: no CR or SR number and no document,
because nothing in Azure changes), and the engineer presses Approve on a card that adds 0, changes 0 and removes 0. Approving
writes what Azure looks like now into the state (the executor, as the read identity) and marks the lines seen as accepted.

There is no "put back": no action, argument or code path sets Azure to what the template says. A template that still says the
old value will plan a normal change for it on its own card with its own number. Putting something back by hand is a suggestion
(`azure_change suggest`) with a runbook. A record of this kind can never be applied: it has no commit, no reference and no
document, so every condition of `azure_apply` refuses it, and `azure_apply` and the gate only run an approved `azure_change`.
A draft on a workspace with unaccepted drift is refused (`draft_problem`) until it is accepted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from helpers import db, user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import (
    AWAITING_APPROVAL, CANCELLED, CLOSED, FAILED, FROM_REVIEWED_CLI, REJECTED, SUPERSEDED, ChangeRecord,
)
from plugins._ea_azure_change.helpers import card, executor_client, records
from plugins._ea_inventory.helpers import redact

log = logging.getLogger(__name__)

PLAYBOOK = "drift_accept"
#: What was seen goes stale: an acceptance card lives a day, not the fourteen of a change.
VALID_SECONDS = 86400
MAX_RESOURCES = 20
SHOWN_LINES = 6
PUT_BACK = ("Nothing is put back: the product never sets Azure to what a template says. If someone should undo it, write a "
            "suggestion for the engineer (azure_change suggest); a template that still says the old value will plan a normal "
            "change for it, with its own card and its own CR or SR number.")


def is_accept(record: ChangeRecord) -> bool:
    return record.kind == "azure_change" and record.playbook_name == PLAYBOOK


def _store():
    from plugins._ea_azure.helpers import store

    return store


def _line(text: str) -> str:
    """A value from the stored inventory on its way to a card, a record or the model: through the redactor, like any other."""
    return redact.redact_text(text)[0]


def _same(workspace: str, subscription: str, resource_group: str) -> bool:
    return str(workspace).lower() == f"{subscription}/{resource_group}".lower()


# --- the draft guard ---------------------------------------------------------------------------------------------


def _sentence(found: list[dict[str, Any]]) -> str:
    names = ", ".join(f"{d['name']} ({d['lines'][-1]})" if d["lines"] else d["name"] for d in found[:5])
    return (f"{len(found)} resource(s) the product made changed in Azure outside the product and nobody has accepted that yet: {names}. "
            "A change is not drafted on top of drift. Tell the engineer; call azure_change with action drift to list what changed, and "
            "drift_accept with the resource to put the acceptance on a card for them. " + PUT_BACK)


def draft_problem(subscription: str, resource_group: str) -> str | None:
    """Why a draft on this workspace is refused, or None. Best effort on the stored inventory (it never stops a draft on its own
    failure); the plan policy is the boundary, this is the engineer's chance to look first."""
    try:
        found = [d for d in _store().drifted() if _same(d["workspace"], subscription, resource_group)]
    except Exception as error:  # noqa: BLE001 - no database, no inventory: nothing to compare with
        log.info("azure drift: not checked (%s)", type(error).__name__)
        return None
    return _sentence(found) if found else None


def resource_problem(native_id: str) -> str | None:
    """Why a simple action on this one resource is refused, or None. For the action tool's draft (`azure_action`)."""
    try:
        found = [d for d in _store().drifted() if d["native_id"] == str(native_id).lower()]
    except Exception as error:  # noqa: BLE001
        log.info("azure drift: not checked (%s)", type(error).__name__)
        return None
    return _sentence(found) if found else None


# --- the report --------------------------------------------------------------------------------------------------


def report(args: dict[str, Any]) -> str:
    """`azure_change drift`: the drifted resources and what changed, from the stored inventory. Never contacts Azure."""
    store = _store()
    last = store.as_of()["last_pull"]
    if not last:
        return "No Azure inventory has been pulled yet, so nothing can be compared."
    filters = {k: str(args[k]) for k in ("subscription", "resource_group") if args.get(k)}
    found = store.drifted(filters)
    stamp = f"Inventory of {last['started_at']}."
    if not found:
        return f"{stamp} No resource the product made has changed in Azure outside the product (since its last apply or acceptance)."
    out = [f"{stamp} {len(found)} resource(s) the product made changed in Azure outside it, with no apply to explain it:"]
    for d in found[:MAX_RESOURCES]:
        out.append(f"- {d['name']} ({d['label']}) in {d['resource_group']}, last applied by change {d['change_id'][:8]}:")
        out += [f"    {_line(line)}" for line in d["lines"][-SHOWN_LINES:]]
    return "\n".join(out) + "\n\n" + PUT_BACK + " To record that the engineer accepts what Azure looks like now, call azure_change with action drift_accept and resource=<name or ID>."


# --- the accept card -----------------------------------------------------------------------------------------------


def _origin(change_id: str) -> ChangeRecord | None:
    """The template change that last applied a resource: its template and values name the state that holds it."""
    try:
        found, _owner = change_store.find_across_users(change_id)
    except change_store.ChangeNotFound:
        return None
    return found if found.kind == "azure_change" and found.playbook_name in records.templates() else None


def _lineage(origin: ChangeRecord) -> tuple[str, str, str]:
    return origin.limit.lower(), origin.playbook_name, json.dumps(origin.variables, sort_keys=True, default=str)


def accept_card_args(record: ChangeRecord) -> dict[str, Any]:
    pf, scope = record.preflight, record.preflight.get("scope") or {}
    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    return dict(
        prompt=record.summary,
        endpoint=card.APPROVE,
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        approve_label="Accept what Azure looks like now",
        reject_label="Leave it",
        consequence="Adds 0, changes 0, removes 0. Azure is not touched; the product's record of it is brought up to date.",
        details=[(k, v) for k, v in (
            ("Subscription", str(scope.get("subscription") or "")), ("Resource group", str(scope.get("resource_group") or "")),
            ("Template", str(record.variables.get("template") or "")), ("Resources", ", ".join(pf.get("names") or [])),
            ("Applies as", "the cloud executor's read identity, which cannot change Azure"),
            ("CR or SR number", "not needed: nothing in Azure changes"),
            ("After you accept", "the template is not changed. If it still says the old value, a later draft will plan a normal "
                                 "change for it on its own card with its own number. Nothing is put back by this.")) if v],
        checks=card._checks(record),
        diff=card._fit(pf.get("lines") or [], 60),
        rollback=["Nothing to back out: Azure is unchanged. The state is copied before it is rewritten."],
        expires_at=record.expires_at,
        tier="change-low",
    )


def accept_card(record: ChangeRecord) -> dict[str, str]:
    from plugins._ea_interaction.helpers import cards

    return cards.decision_card(**accept_card_args(record))


def _plan_problem(answer: dict[str, Any]) -> str | None:
    if not answer.get("ok"):
        return f"The refresh could not be planned at the '{answer.get('stage') or 'plan'}' stage: {answer.get('refusal') or 'no reason given'}"
    if not isinstance(answer.get("drifted"), list) or not re.fullmatch(r"[0-9a-f]{64}", str(answer.get("plan_digest") or "")):
        return "The cloud executor does not support refresh-only plans yet, so nothing can be accepted."
    if any(not c.get("ok") for c in answer.get("checks") or [] if c.get("level") != "warn"):
        return "A check failed on the refresh-only plan, so nothing can be accepted."
    summary = answer.get("summary") or {}
    if any(int(summary.get(k) or 0) for k in ("add", "change", "import")) or summary.get("resources"):
        return "The refresh-only plan lists changes, so it was refused. It must change nothing."
    return None


async def offer(agent: Any, args: dict[str, Any]) -> str:
    """`azure_change drift_accept`: plan the refresh, save the approval record and show its card. Nothing in Azure changes."""
    store = _store()
    ref = str(args.get("resource") or "").strip()
    if not ref:
        return "drift_accept needs `resource`: the name or ID of a drifted resource from azure_change drift."
    try:
        row = store.get(ref)
    except store.Ambiguous:
        return f"`{ref}` matches more than one stored resource. Use its full resource ID."
    drifted = {d["native_id"]: d for d in store.drifted()}
    target = drifted.get(row["native_id"]) if row else None
    if not target:
        return f"`{ref}` has not drifted: nothing to accept. Call azure_change with action drift to list what has."
    origin = _origin(target["change_id"])
    if origin is None:
        return ("The change that made that resource is not on record as a template change, so there is no state to refresh. "
                "Tell the engineer; nothing was drafted.")
    group = [d for d in drifted.values() if (o := _origin(d["change_id"])) is not None and _lineage(o) == _lineage(origin)][:MAX_RESOURCES]
    scope, requester, change_id = records.workspace_of(origin.limit), user_scope.get_current_user_id() or "", records.new_id()
    try:
        answer = await asyncio.to_thread(executor_client.plan, agent, source={"template": origin.playbook_name, "variables": origin.variables},
                                         workspace=scope, change_id=change_id, actor=requester, mode="refresh_only")
    except executor_client.ExecutorError as error:
        return f"The refresh could not be planned, so nothing was shown: {error}"
    if problem := _plan_problem(answer):
        return f"{problem}\n\nNothing was drafted and there is no card."
    inventory = [_line(f"{d['name']}: {line}") for d in group for line in d["lines"][-SHOWN_LINES:]]
    names = [d["name"] for d in group]
    now = time.time()
    record = ChangeRecord(
        id=change_id, created_at=now, requested_by=requester, status=AWAITING_APPROVAL, kind="azure_change", playbook_name=PLAYBOOK,
        variables={"template": origin.playbook_name, "values": origin.variables, "through": {d["native_id"]: d["through"] for d in group}},
        limit=origin.limit, summary=f"Accept what Azure looks like now for {len(group)} resource(s) in {scope['resource_group']}",
        commands=[*answer["drift"], *inventory], render_method=FROM_REVIEWED_CLI, rendered_at=now, expires_at=now + VALID_SECONDS,
        plan_digest=str(answer["plan_digest"]), tier="change-low",
        preflight={"scope": {**scope, "region": ""}, "summary": {"add": 0, "change": 0, "import": 0}, "cost": {}, "names": names,
                   "lines": [*answer["drift"], *inventory], "state_drift": len(answer["drifted"]), "purpose": "Accept drift (state only)",
                   "checks": [{"label": "Nothing in Azure changes", "ok": True, "detail": "the executor only reads Azure; the state is copied first"},
                              {"label": "State differences seen by the executor", "ok": True, "detail": str(len(answer["drifted"]))},
                              {"label": "Inventory lines on the card", "ok": True, "detail": str(len(inventory))}]},
    )
    change_store.save(record)  # audited as azure.change.drafted
    try:
        card.show(agent, record, accept_card(record))
    except Exception as error:  # noqa: BLE001 - the card is the only way to decide; without it the record is dead
        record.status = CANCELLED
        change_store.save(record)
        return f"The acceptance card could not be shown, so it was cancelled: {error}"
    shown = "\n".join(f"  {line}" for line in record.commands[:30])
    return (f"Acceptance `{record.id}` drafted for {', '.join(names[:5])}. It adds 0, changes 0 and removes 0: Azure is not touched.\n"
            f"What differs:\n```\n{shown}\n```\n{PUT_BACK}\n\nStop here and wait: do not call another tool. I cannot approve this. The engineer "
            "reads the card and presses Accept; no CR or SR number is needed.")


# --- the decision (called by `api/azure_approve.py` once it has checked who, status and expiry) ------------------------


def _mark(through: dict[str, str]) -> None:
    with db.transaction(db.NO_SCOPE) as conn:
        for native, at in through.items():
            conn.execute("UPDATE cloud_managed SET accepted_at = GREATEST(COALESCE(accepted_at, '-infinity'), %s::timestamptz) "
                         "WHERE native_id = %s", (at, str(native).lower()))


def _summary(record: ChangeRecord) -> dict[str, Any]:
    return {"id": record.id, "status": record.status, "requested_by": record.requested_by, "approved_by": record.approved_by,
            "approved_hash": record.approved_hash, "reference": ""}


async def decide(record: ChangeRecord, owner: str, decision: str, expected_hash: str) -> dict[str, Any]:
    """Approve or reject an acceptance. Approving needs the hash the card carried and no number. The executor writes the state
    (only when it saw a difference) and only for the digest approved; then the lines seen are marked accepted."""
    if decision == "reject":
        record.status = REJECTED
        change_store.save(record, owner)
        return {"ok": True, "change": _summary(record)}
    if expected_hash != record.current_hash:
        return {"ok": False, "error": "What you saw is no longer what would be accepted. Ask for a fresh acceptance."}
    # Approved and used in one call: the register holds both, so the same card cannot be accepted twice.
    if not (change_store.approve(record, owner, owner) and change_store.take(record, owner)):
        return {"ok": False, "error": f"Change {record.id} is no longer awaiting approval, so nothing was accepted."}
    if record.preflight.get("state_drift"):
        try:
            answer = await asyncio.to_thread(
                executor_client.refresh_accept, None, source={"template": record.variables["template"], "variables": record.variables["values"]},
                workspace=records.workspace_of(record.limit), plan_digest=record.plan_digest, change_id=record.id, actor=owner)
        except executor_client.ExecutorError as error:
            answer = {"ok": False, "refusal": str(error)}
        if not answer.get("ok"):
            record.status = SUPERSEDED if answer.get("superseded") else FAILED
            record.error = str(answer.get("refusal") or "The executor refused.")[:500]
            change_store.save(record, owner)
            return {"ok": False, "error": f"Nothing was accepted: {record.error}"}
    _mark(record.variables.get("through") or {})
    record.status = CLOSED
    change_store.save(record, owner)
    if change_store.audit is not None:
        change_store.audit.emit("azure.drift.accepted", change_id=record.id, plan_digest=record.plan_digest, requested_by=record.requested_by,
                                approved_by=owner, resources=len(record.variables.get("through") or {}), state_drift=record.preflight.get("state_drift", 0))
    return {"ok": True, "change": _summary(record)}
