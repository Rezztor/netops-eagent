"""A simple Azure action: start something that is stopped, add instances, add tags. Seven actions, all additive.

`draft` asks the cloud executor what one reviewed ARM call would do to one resource from the stored inventory, accepts the
answer only if it is one POST or PATCH and no check failed, writes the change document, saves an `azure_action` record and
shows the same decision card a template change gets (no Edit button: the values are one or two). The engineer approves with a
CR or SR number after downloading the document (`api/azure_approve.py`). `apply` is gated (`_95_azure_change_gate.py`), runs
the conditions again, and has the executor read Azure again and make the call only if nothing moved since the approval.

There is no argument for a removal, a stop, a restart, a resize or a scale-in, and none can be added by a caller: the action
list is the executor's `actions.yaml`, closed in `cloud_executor/actions.py`. Such a need is `azure_change suggest`.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import yaml

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPROVED, AWAITING_APPROVAL, CANCELLED, FAILED, FROM_REVIEWED_CLI, ChangeRecord
from plugins._ea_azure_change.helpers import card, conditions, drift, executor_client, records, tier

ACTIONS_FILE = Path(records.TEMPLATES_FILE).with_name("actions.yaml")
SUMMARY = {"start": "Start", "scale_out": "Add instances to", "tags": "Add tags to"}
_ID = re.compile(r"/subscriptions/([^/]+)/resourcegroups/([^/]+)/", re.IGNORECASE)
_CALL = re.compile(r"(POST|PATCH) https://management\.azure\.com/\S+")
STOP = ("Stop here and wait: do not call another tool. I cannot approve this. Tell the engineer these three steps, in this order, "
        "and no others: " + records.STEPS + " Typing the number or the word Approve in the chat approves nothing. When they say they "
        "have approved, call azure_action with action=apply and this change_id.")


def _say(text: str) -> Response:
    return Response(message=text, break_loop=False)


def catalogue() -> dict[str, dict[str, Any]]:
    """The reviewed actions by name, from the executor's list shipped beside this code."""
    try:
        return {e["name"]: e for e in yaml.safe_load(ACTIONS_FILE.read_text(encoding="utf-8")) or [] if isinstance(e, dict) and e.get("name")}
    except (OSError, yaml.YAMLError):
        return {}


def _kind(name: str) -> str:
    return "tags" if name == "tags_merge" else "scale_out" if name.endswith("_scale_out") else "start"


def _listing() -> str:
    needs = {"start": "nothing", "scale_out": "`capacity` (the new total, higher than now)", "tags": "`tags` (name and value pairs)"}
    return "\n".join(f"- {n}: acts on {e.get('resource_type')}. Needs: {needs[_kind(n)]}." for n, e in sorted(catalogue().items())) or "- none"


def accept(answer: dict[str, Any], name: str, ceiling: float | None) -> None:
    """Defence in depth on the executor's answer: one POST or PATCH to the management host, one resource, no failed check."""
    if not answer.get("ok"):
        raise records.PlanRefused(f"The action was refused at the '{answer.get('stage') or 'check'}' stage: {answer.get('refusal') or 'no reason given'}")
    resources = (answer.get("summary") or {}).get("resources") or []
    if answer.get("action") != name or not _CALL.match(str(answer.get("line") or "")) or len(resources) != 1 or not answer.get("plan_digest"):
        raise records.PlanRefused("The executor's answer is not one reviewed call on one resource, so it was refused.")
    failed = [c for c in answer.get("checks") or [] if not c.get("ok") and c.get("level") != "warn"]
    if failed:
        raise records.PlanRefused("A check failed: " + "; ".join(f"{c.get('label')}: {c.get('detail') or 'failed'}" for c in failed))
    monthly = (answer.get("cost") or {}).get("monthly_usd")
    if ceiling is not None and monthly is not None and float(monthly) > float(ceiling):
        raise records.PlanRefused(f"About {monthly} USD a month is over the {ceiling} USD ceiling for one change.")


def make_record(answer: dict[str, Any], name: str, *, change_id: str, requester: str, resource_id: str, variables: dict[str, Any], commit: str,
                purpose: str, agent: Any | None = None) -> ChangeRecord:
    """The `azure_action` record for an answer `accept` took. Not saved."""
    rail = records.rails(agent)
    summary, price, scope = answer["summary"], answer.get("cost") or {}, answer.get("scope") or {}
    level, reasons = tier.decide({"risk": answer.get("risk")}, {"summary": summary, "cost": price}, rail)
    verb = SUMMARY[_kind(name)]
    resource = summary["resources"][0]["name"]
    checks = [{"label": str(c.get("label")), "ok": bool(c.get("ok")), **({"level": c["level"]} if c.get("level") else {}),
               "detail": str(c.get("detail") or "")} for c in answer.get("checks") or []]
    checks.append({"label": "Nothing is deleted, stopped or restarted", "ok": True, "detail": "one additive call: " + name})
    now = time.time()
    return ChangeRecord(
        id=change_id, created_at=now, requested_by=requester, status=AWAITING_APPROVAL, kind="azure_action", playbook_name=name, commit=commit,
        variables=variables, limit=resource_id, summary=records._one_line(purpose) or f"{verb} {resource}", commands=[str(answer["line"])],
        diff=[str(x) for x in summary.get("lines") or []], render_method=FROM_REVIEWED_CLI, rendered_at=now,
        rollback_commands=[x for x in str(answer.get("backout") or "").splitlines() if x.strip()], expires_at=now + records.valid_days(agent) * records.DAY,
        plan_digest=str(answer["plan_digest"]), tier=level, confirm="",
        preflight={"scope": {"subscription": scope.get("subscription", ""), "resource_group": scope.get("resource_group", ""),
                             "region": scope.get("region", ""), "rg_new": False},
                   "summary": summary, "cost": price, "checks": checks, "advice": [], "risk_reasons": reasons,
                   "backout": str(answer.get("backout") or ""), "purpose": records._one_line(purpose, 1000), "usd_month_per_change": rail["usd_month_per_change"],
                   "drift": [], "plan_digest": str(answer["plan_digest"]), "requires": str(answer.get("requires") or ""), "resource": resource},
    )


def action_card(record: ChangeRecord) -> dict[str, str]:
    """The change card, said in the words of one call on one resource. No Edit button: an action has a name and one or two values."""
    from plugins._ea_interaction.helpers import cards

    pf, price = record.preflight, record.preflight.get("cost") or {}
    scope, monthly = pf.get("scope") or {}, price.get("monthly_usd")
    cost_text = (f"about {monthly} USD a month more, list price ({price.get('note')}; prices of {price.get('priced_at')})" if monthly is not None
                 else str(price.get("note") or "not priced"))
    details = [("Subscription", scope.get("subscription", "")), ("Resource group", scope.get("resource_group", "")),
               ("Resource", f"{pf.get('resource')} ({record.limit})"[:280]), ("Action", f"{record.playbook_name} ({record.commit[:10]})"),
               ("Applies as", "the cloud executor, with the change application's own identity, never yours"), ("Cost", cost_text),
               ("Change document", f"{record.id[:8]}, issued {card._day(record.created_at)}, digest {record.plan_digest[:16]}")]
    if pf.get("risk_reasons") and record.tier == "change-high":
        details.insert(5, ("Why high risk", "; ".join(str(r) for r in pf["risk_reasons"])))
    args = {**card.change_card_args(record), "details": [(k, v) for k, v in details if v], "edit_endpoint": None, "edit_payload": None,
            "consequence": f"One call on one resource. Removes nothing, stops nothing, restarts nothing. {'Cost: ' + cost_text if monthly is not None else ''}".strip()}
    return cards.decision_card(**args)


class AzureAction(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "draft").strip().lower()
        if not records.enabled():
            return _say("Azure changes are switched off on this instance.")
        if action not in ("draft", "apply"):
            return _say(f"Unknown action '{action}'. Use draft or apply. Nothing here removes, stops, restarts, resizes or scales in.")
        if not executor_client.is_configured(self.agent):
            return _say("Azure changes are not configured on this instance. An administrator must deploy the cloud executor and set its token.")
        return await (self._draft(args) if action == "draft" else self._apply(args))

    # --- draft -----------------------------------------------------------------------------------------------------

    async def _draft(self, args: dict[str, Any]) -> Response:
        from plugins._ea_azure.helpers import store

        name = str(args.get("name") or "").strip()
        known = catalogue()
        if name not in known:
            return _say(f"{'There is no action named `' + name + '`. ' if name else ''}Actions that exist:\n{_listing()}\n"
                        "To remove or stop something, write a suggestion with azure_change suggest.")
        ref = str(args.get("resource") or "").strip()
        try:
            row = store.get(ref) if ref else None
        except store.Ambiguous:
            return _say(f"`{ref}` matches more than one stored resource. Use its full resource ID.")
        if not row or row.get("removed_at"):
            return _say(f"`{ref}` is not a live resource in the stored Azure inventory, and an action is only drafted on what the inventory lists.")
        resource_id = str(row["native_id"])
        if problem := drift.resource_problem(resource_id):  # changed outside the product and not yet accepted: no new draft on it
            return _say(problem)
        variables = self._variables(name, args)
        if isinstance(variables, str):
            return _say(variables)
        requester, change_id = user_scope.get_current_user_id() or "", records.new_id()
        try:
            health = await asyncio.to_thread(executor_client.health, self.agent)
            commit = str(health.get("actions_commit") or "")
            if commit in ("", "none"):  # "none" is what /healthz says when the image was built without the action list's commit
                raise records.PlanRefused("The cloud executor reports no action list commit, so nothing can be pinned to a reviewed version.")
            answer = await asyncio.to_thread(executor_client.action_preview, self.agent, name=name, resource_id=resource_id, variables=variables,
                                             change_id=change_id, actor=requester)
            accept(answer, name, records.rails(self.agent)["usd_month_per_change"])
            record = make_record(answer, name, change_id=change_id, requester=requester, resource_id=resource_id, variables=variables, commit=commit,
                                 purpose=str(args.get("purpose") or ""), agent=self.agent)
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return _say(f"{error}\n\nNothing was drafted and there is no card. Tell the user why. Do not try to get the same result another way.")
        records.store_new(record)
        try:
            card.show(self.agent, record, action_card(record))
        except Exception as error:  # noqa: BLE001 - the card is the only way to approve; without it the record is dead
            record.status = CANCELLED
            change_store.save(record)
            return _say(f"The approval card could not be shown, so the change was cancelled: {error}")
        listing = "\n".join(f"  {line}" for line in record.diff[:10])
        return _say(f"Change `{record.id}` drafted: {record.summary}.\nTier: {record.tier}. Document {record.id[:8]}, digest {record.plan_digest[:16]}, "
                    f"valid until {card._day(record.expires_at)}.\nWhat it does:\n```\n{listing}\n{record.commands[0]}\n```\n\n{STOP}")

    @staticmethod
    def _variables(name: str, args: dict[str, Any]) -> dict[str, Any] | str:
        """The typed values for this action, or the sentence that says what is missing. The executor checks them again."""
        kind = _kind(name)
        if kind == "start":
            return {}
        if kind == "scale_out":
            capacity = args.get("capacity")
            if isinstance(capacity, str) and capacity.strip().isdigit():
                capacity = int(capacity)
            return {"capacity": capacity} if isinstance(capacity, int) and not isinstance(capacity, bool) else f"{name} needs `capacity`: the new total number of instances, a whole number."
        tags = args.get("tags")
        return {"tags": tags} if isinstance(tags, dict) and tags else f"{name} needs `tags`: the names and values to add or change."

    # --- apply -----------------------------------------------------------------------------------------------------

    async def _apply(self, args: dict[str, Any]) -> Response:
        change_id = str(args.get("change_id") or "").strip()
        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return _say(str(error))
        if record.kind != "azure_action":
            return _say(f"`{record.id}` is not a simple action. A template change is applied with azure_apply.")
        actor = user_scope.get_current_user_id() or ""
        if record.status != APPROVED:
            return _say(records.pass_on(f"Change {record.id} is '{record.status}'. Only an approved change can be applied; for one that is applying, call azure_change status."))
        refusal = conditions.first_failure(record, actor)
        if refusal:
            return _say(records.pass_on(refusal))
        with records.in_flight(record.id) as mine:  # the settling loop leaves this change alone while this call waits on it
            if not mine:
                return _say(f"Change `{record.id}` is already being applied by another call. Check it later with azure_change status.")
            return await self._run(record, actor)

    async def _run(self, record: Any, actor: str) -> Response:
        if not change_store.take(record):  # one statement in the approval register: an approval runs once
            return _say(records.pass_on(f"Change {record.id} has no approval left to use: it was already applied, or is being applied. Nothing ran."))
        try:
            record.job_id = await asyncio.to_thread(
                executor_client.action_run, self.agent, name=record.playbook_name, resource_id=record.limit, variables=record.variables,
                plan_digest=record.plan_digest, reference=record.reference, change_id=record.id, actor=actor)
            change_store.save(record)
            job = await asyncio.to_thread(executor_client.wait, self.agent, record.job_id, executor_client.config(self.agent)["apply_wait_seconds"])
        except executor_client.ExecutorBusy as busy:  # turned away before anything ran (six requests a minute): the approval stands
            change_store.give_back(record)
            return _say(f"Change `{record.id}` did not start: {busy} Nothing ran and the approval stands. Tell the user, and apply it again in a minute.")
        except executor_client.ExecutorError as error:
            if not record.job_id:  # refused before a job existed: nothing ran
                record.status, record.error = FAILED, str(error)[:500]
                change_store.save(record)
                return _say(f"The change did not run: {error}")
            return _say(f"Change `{record.id}` was started as job {record.job_id}, but its state could not be read now: {error}. "
                        "Tell the user; check it later with azure_change status.")
        if job.get("status") not in executor_client.TERMINAL:
            return _say(f"Change `{record.id}` is still applying (job {record.job_id}). Tell the user, and check it later with azure_change status.")
        # An action makes nothing, so nothing is listed as made by the product.
        return _say(records.finish(record, {**job, "changed_ids": []}, actor))
