"""Adoption: bring resources that already exist in Azure under the product's records, without changing them (ADR 0071).

The agent names a subscription and resource group (and, if it wants fewer, some resources). The resources come from the **stored
inventory**, never from the model: each is `{id, name, location, tags, parent}` as the last pull saw it. The cloud executor makes
the OpenTofu import code itself from those values (`cloud_executor/adopt.py`), plans it in `import` mode and refuses any plan
that would add, change, replace or delete anything: **importing N, changes 0**, or nothing is shown.

Approving writes the imports into the state and lists the resources in `cloud_managed` (so a later pull's differences are drift,
and a later change knows they are the product's). It changes nothing in Azure, so the record (`kind` `azure_change`, playbook
`adopt`) needs the engineer's Approve on the card and **no CR or SR number and no document**, the same as accepting drift. A record
of this kind can never be applied: it has no commit, no reference and no document, so every condition of `azure_apply` refuses it.
Removing anything stays a suggestion, adopted or not.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from helpers import user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import (
    APPLIED, AWAITING_APPROVAL, CANCELLED, FAILED, FROM_REVIEWED_CLI, REJECTED, SUPERSEDED, ChangeRecord,
)
from plugins._ea_azure_change.helpers import card, executor_client, records

log = logging.getLogger(__name__)

PLAYBOOK = "adopt"
#: An adoption is only as true as the last pull: the card lives a day, not the fourteen of a change.
VALID_SECONDS = 86400
SHOWN = 12


def is_adopt(record: ChangeRecord) -> bool:
    return record.kind == "azure_change" and record.playbook_name == PLAYBOOK


def _engine() -> Any | None:
    """The executor's adoption module (its type table and ID reader), or None where it is not in this image: nothing is then offered."""
    try:
        from cloud_executor import adopt as engine

        return engine
    except ImportError:  # pragma: no cover - exercised by a test that blocks the import
        return None


# --- what can be offered ------------------------------------------------------------------------------------------


def _rows(subscription: str, resource_group: str) -> list[dict[str, Any]]:
    """The live stored resources of one group. The filter matches names, so the ID prefix settles which group is meant."""
    from plugins._ea_azure.helpers import store

    found, _ = store.search({"subscription": subscription, "resource_group": resource_group}, limit=500)
    prefix = f"/subscriptions/{subscription.lower()}/resourcegroups/{resource_group.lower()}/"
    return [r for r in found if str(r["native_id"]).startswith(prefix)]


def _managed() -> set[str]:
    """IDs the product already records as its own (a resource is adopted once, into one state)."""
    from plugins._ea_inventory.helpers import store as inv

    with inv.transaction() as conn:
        return {str(r["native_id"]).lower() for r in inv.rows(conn, "SELECT native_id FROM cloud_managed WHERE removed_at IS NULL")}


def select(subscription: str, resource_group: str, wanted: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[str]], str]:
    """`(items, skipped, problem)`: what to adopt from the stored inventory, what was left out and why, or why nothing can be offered."""
    engine = _engine()
    if engine is None:
        return [], {}, "Adoption is not installed in this image, so nothing can be adopted."
    rows = _rows(subscription, resource_group)
    if not rows:
        return [], {}, (f"The stored inventory has no live resources in `{resource_group}` for that subscription. Pull the inventory first, "
                        "or check the subscription GUID and the group name.")
    if wanted:
        asked = {w.strip().lower() for w in wanted if w.strip()}
        picked = [r for r in rows if r["native_id"] in asked or r["name"].lower() in asked]
        for name in sorted(asked):
            hits = [r for r in picked if r["native_id"] == name or r["name"].lower() == name]
            if not hits:
                return [], {}, f"`{name}` is not a live resource of `{resource_group}` in the stored inventory."
            if len(hits) > 1 and not any(r["native_id"] == name for r in hits):
                return [], {}, f"`{name}` matches more than one resource in `{resource_group}`. Use its full resource ID."
        rows = picked
    managed = _managed()
    chosen, skipped = [], {"already managed by the product": [], "type that cannot be adopted": []}
    for r in rows:
        if r["native_id"] in managed:
            skipped["already managed by the product"].append(r["name"])
        elif not engine.describe(r["native_id"]):
            skipped["type that cannot be adopted"].append(f"{r['name']} ({r['type']})")
        else:
            chosen.append({"id": r["native_id"], "name": r["name"], "location": r["region"] or "", "tags": r["tags"] or {},
                           "parent": (r.get("parent") or {}).get("name") or ""})
    return chosen, {k: v for k, v in skipped.items() if v}, ""


def _skipped_text(skipped: dict[str, list[str]]) -> str:
    return " ".join(f"Left out, {why}: {', '.join(names[:8])}{' and more' if len(names) > 8 else ''}." for why, names in skipped.items())


# --- the card ------------------------------------------------------------------------------------------------------


def adopt_card_args(record: ChangeRecord) -> dict[str, Any]:
    pf, scope = record.preflight, record.preflight.get("scope") or {}
    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    count = len(record.variables.get("adopt") or [])
    return dict(
        prompt=record.summary,
        endpoint=card.APPROVE,
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        approve_label="Bring under management",
        reject_label="Not now",
        consequence=f"Imports {count}, adds 0, changes 0, removes 0. Azure is not touched; the product starts to record these resources as its own.",
        details=[(k, v) for k, v in (
            ("Subscription", str(scope.get("subscription") or "")), ("Resource group", str(scope.get("resource_group") or "")),
            ("Resources", ", ".join(pf.get("names") or [])[:600]), ("Left out", str(pf.get("skipped_text") or "")),
            ("Applies as", "the cloud executor's read identity, which cannot change Azure"),
            ("CR or SR number", "not needed: nothing in Azure changes"),
            ("After you approve", "later changes to these resources are planned, judged and approved as usual. Removing any of them "
                                  "is still only ever a suggestion.")) if v],
        checks=card._checks(record),
        diff=card._fit(pf.get("lines") or [], 60),
        rollback=["Nothing to back out: Azure is unchanged. The state is copied before the imports are written."],
        expires_at=record.expires_at,
        tier="change-low",
    )


def adopt_card(record: ChangeRecord) -> dict[str, str]:
    from plugins._ea_interaction.helpers import cards

    return cards.decision_card(**adopt_card_args(record))


def _plan_problem(answer: dict[str, Any], count: int) -> str | None:
    """Defence in depth on the executor's answer: an adoption plan imports exactly `count` and changes 0."""
    if not answer.get("ok"):
        return f"The adoption could not be planned at the '{answer.get('stage') or 'plan'}' stage: {answer.get('refusal') or 'no reason given'}"
    if not re.fullmatch(r"[0-9a-f]{64}", str(answer.get("plan_digest") or "")):
        return "The plan carries no digest, so there is nothing to approve."
    if any(not c.get("ok") for c in answer.get("checks") or [] if c.get("level") != "warn"):
        return "A check failed on the adoption plan, so nothing can be adopted."
    summary = answer.get("summary") or {}
    resources = summary.get("resources") or []
    if int(summary.get("add") or 0) or int(summary.get("change") or 0) or int(summary.get("import") or 0) != count \
            or len(resources) != count or any(str(r.get("action")) != "import" for r in resources):
        return f"The plan is not 'importing {count}, changes 0', so it was refused. Adoption proves nothing in Azure changes."
    return None


async def offer(agent: Any, args: dict[str, Any]) -> str:
    """`azure_change adopt`: choose from the stored inventory, plan the import, save the record and show its card."""
    subscription, sub_problem = records.resolve_subscription(args.get("subscription"))
    if sub_problem:
        return sub_problem
    group = str(args.get("resource_group") or "").strip()
    if problem := records.scope_problem(subscription, group):
        return f"{problem} Adoption needs `subscription` and `resource_group`; `resources` (names or IDs) is optional."
    raw = args.get("resources")
    wanted = [x.strip() for x in re.split(r"[,\n]", raw) if x.strip()] if isinstance(raw, str) else [str(x).strip() for x in raw or []]
    chosen, skipped, problem = select(subscription, group, wanted)
    if problem:
        return problem
    note = _skipped_text(skipped)
    if not chosen:
        return f"Nothing in `{group}` can be adopted. {note}"
    limit = int(records.rails(agent)["max_resources"])
    if len(chosen) > limit:
        return (f"{len(chosen)} resources is more than the limit of {limit} for one change. Adopt a part of `{group}` at a time by naming "
                f"them in `resources`. {note}")
    scope, requester, change_id = {"subscription": subscription, "resource_group": group}, user_scope.get_current_user_id() or "", records.new_id()
    try:
        answer = await asyncio.to_thread(executor_client.plan, agent, source={"adopt": chosen}, workspace=scope, change_id=change_id,
                                         actor=requester, mode="import")
    except executor_client.ExecutorError as error:
        return f"The adoption could not be planned, so nothing was shown: {error}"
    if problem := _plan_problem(answer, len(chosen)):
        return f"{problem}\n\nNothing was drafted and there is no card. Do not try to get the same result another way."
    names = [c["name"] for c in chosen]
    lines = [str(x) for x in (answer.get("summary") or {}).get("lines") or []]
    now = time.time()
    record = ChangeRecord(
        id=change_id, created_at=now, requested_by=requester, status=AWAITING_APPROVAL, kind="azure_change", playbook_name=PLAYBOOK,
        variables={"adopt": chosen}, limit=f"{subscription}/{group}", rendered_at=now, render_method=FROM_REVIEWED_CLI,
        summary=f"Bring {len(chosen)} existing resource(s) in {group} under management", expires_at=now + VALID_SECONDS,
        commands=[f"{c['name']}  {c['id']}" for c in chosen], diff=lines, plan_digest=str(answer["plan_digest"]), tier="change-low",
        preflight={"scope": {**scope, "region": ""}, "summary": answer["summary"], "cost": answer.get("cost") or {}, "names": names,
                   "lines": lines, "skipped_text": note, "purpose": "Adopt existing resources (state only)",
                   "checks": [*[{"label": str(c.get("label")), "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")}
                                for c in answer.get("checks") or []],
                              {"label": "Nothing in Azure changes", "ok": True, "detail": "an import writes the state; the executor reads Azure"}]},
    )
    change_store.save(record)  # audited as azure.change.drafted
    try:
        card.show(agent, record, adopt_card(record))
    except Exception as error:  # noqa: BLE001 - the card is the only way to decide; without it the record is dead
        record.status = CANCELLED
        change_store.save(record)
        return f"The adoption card could not be shown, so it was cancelled: {error}"
    shown = "\n".join(f"  {line}" for line in lines[:SHOWN]) + (f"\n  ... {len(lines) - SHOWN} more" if len(lines) > SHOWN else "")
    return (f"Adoption `{record.id}` drafted: importing {len(chosen)}, changes 0, in `{group}`. Azure is not touched.\n```\n{shown}\n```\n{note}\n\n"
            "Stop here and wait: do not call another tool. I cannot approve this. The engineer reads the card and presses Bring under "
            "management; no CR or SR number is needed.")


# --- the decision (called by `api/azure_approve.py` once it has checked who, status and expiry) ------------------------


def _summary(record: ChangeRecord) -> dict[str, Any]:
    return {"id": record.id, "status": record.status, "requested_by": record.requested_by, "approved_by": record.approved_by,
            "approved_hash": record.approved_hash, "reference": ""}


async def decide(record: ChangeRecord, owner: str, decision: str, expected_hash: str) -> dict[str, Any]:
    """Approve or reject an adoption. Approving needs the hash the card carried and no number. The executor writes the state
    only for the digest approved (as the read identity); then the resources are listed as the product's."""
    if decision == "reject":
        record.status = REJECTED
        change_store.save(record, owner)
        return {"ok": True, "change": _summary(record)}
    if expected_hash != record.current_hash:
        return {"ok": False, "error": "What you saw is no longer what would be adopted. Ask for a fresh adoption."}
    # Approved and used in one call: the register holds both, so the same card cannot be adopted twice.
    if not (change_store.approve(record, owner, owner) and change_store.take(record, owner)):
        return {"ok": False, "error": f"Change {record.id} is no longer awaiting approval, so nothing was adopted."}
    chosen = record.variables.get("adopt") or []
    try:
        answer = await asyncio.to_thread(executor_client.adopt_apply, None, source={"adopt": chosen}, workspace=records.workspace_of(record.limit),
                                         plan_digest=record.plan_digest, change_id=record.id, actor=owner)
    except executor_client.ExecutorError as error:
        answer = {"ok": False, "refusal": str(error)}
    if not answer.get("ok"):
        record.status = SUPERSEDED if answer.get("superseded") else FAILED
        record.error = str(answer.get("refusal") or "The executor refused.")[:500]
        change_store.save(record, owner)
        return {"ok": False, "error": f"Nothing was adopted: {record.error}"}
    adopted = [str(c["id"]).lower() for c in chosen]
    listed = records.record_managed(record, adopted)
    record.status, record.applied_at, record.verified = APPLIED, time.time(), True
    if listed != len(adopted):
        record.error = "The imports are in the state, but not every resource could be listed as managed; adopt again to list them."
    change_store.save(record, owner)
    if change_store.audit is not None:
        change_store.audit.emit("azure.adopt.applied", change_id=record.id, plan_digest=record.plan_digest, requested_by=record.requested_by,
                                approved_by=owner, resources=len(adopted), listed=listed)
    return {"ok": True, "change": _summary(record)}
