"""Draft an Azure change, or write a suggestion, and put it in front of an engineer. Nothing here touches Azure.

`draft` has the cloud executor plan a reviewed template (or free code, where an administrator switched it on; always
high risk), accepts the plan only if nothing in it is removed, stopped or
restarted and no check failed, writes the change document, saves an `azure_change` record and shows the decision card.
The agent cannot approve: the engineer presses Approve on the card with a CR or SR number (`api/azure_approve.py`).
`draft_set` is the same for an ordered set of template steps (ADR 0073): one plan call for the whole set, one record, one card,
one document, one number. `resume` plans the same record again and shows its card again, or supersedes it when the plan moved.
`suggest` is how a removal or a stop is handled: a record and a runbook for the engineer, never a call to the executor (ADR 0065).

There is no `delete` action and no argument that could become one. Applying is `azure_apply`, behind the gate.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, CANCELLED, CLOSED, EXPIRED, FROM_REVIEWED_CLI, ChangeRecord
from plugins._ea_azure_change.helpers import card, executor_client, records, sets
from plugins._ea_azure_change.helpers import drift
from plugins._ea_azure_change.helpers import adopt
from plugins._ea_inventory.helpers import redact

#: Not variables: everything else in the arguments is a value for the template.
RESERVED = {"action", "template", "subscription", "resource_group", "purpose", "change_id", "kind", "resources", "finding",
            "code", "variables", "replaces", "steps", "continues"}
MAX_RESOURCES = 20
_KIND = re.compile(r"[a-z0-9_]{1,40}")
STOP = ("Stop here and wait: do not call another tool. I cannot approve this. Tell the engineer these three steps, in this order, "
        "and no others: " + records.STEPS + " Typing the number or the word Approve in the chat approves nothing. When they say they "
        "have approved, call azure_apply with action=apply and this change_id.")


def _say(text: str) -> Response:
    return Response(message=text, break_loop=False)


class AzureChange(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "draft").strip().lower()
        if not records.enabled():
            return _say("Azure changes are switched off on this instance.")
        if action == "suggest":  # never needs the executor
            return self._suggest(args)
        if action == "drift":  # the stored inventory only, never the executor
            return _say(drift.report(args))
        if not executor_client.is_configured(self.agent):
            return _say("Azure changes are not configured on this instance. An administrator must deploy the cloud executor and set its token.")
        if action == "draft":
            return await self._draft(args)
        if action == "draft_set":
            return await self._draft_set(args)
        if action == "resume":
            return await self._resume(args)
        if action == "status":
            return await self._status(args)
        if action == "list":
            return self._list()
        if action == "cancel":
            return self._cancel(args)
        if action == "drift_accept":  # a refresh-only plan and an approval card; nothing in Azure changes
            return _say(await drift.offer(self.agent, args))
        if action == "adopt":  # an import plan and an approval card; nothing in Azure changes
            return _say(await adopt.offer(self.agent, args))
        return _say(f"Unknown action '{action}'. Use draft, draft_set, resume, suggest, status, list, cancel, drift, drift_accept or adopt. There is no action that removes or stops anything.")

    # --- draft -----------------------------------------------------------------------------------------------------

    async def _draft(self, args: dict[str, Any]) -> Response:
        if args.get("steps") or args.get("continues"):
            return _say("A change set is drafted with action=draft_set, not draft.")
        code, name = args.get("code") or None, "free"
        if code is not None:
            problem = records.free_code_problem(code, self.agent)
            if problem:
                return _say(problem)
            if args.get("template") or not str(args.get("purpose") or "").strip():
                return _say("Free code is drafted with `code`, `subscription`, `resource_group` and `purpose` (what it is for, for the change document), and no `template`.")
        else:
            name = str(args.get("template") or "").strip()
            catalogue = records.templates()
            if not name or (catalogue and name not in catalogue):
                return _say(f"{'There is no template named `' + name + '`. ' if name else ''}Templates that exist:\n{records.catalogue_text()}")
        subscription, sub_problem = records.resolve_subscription(args.get("subscription"))
        if sub_problem:
            return _say(sub_problem)
        group = str(args.get("resource_group") or "").strip()
        problem = records.scope_problem(subscription, group)
        if problem:
            return _say(problem)
        replaced = None
        if str(args.get("replaces") or "").strip():  # a redraft of one still on a card: that card goes quiet once this one exists
            replaced, refusal = self._mine({"change_id": args["replaces"]})
            if not refusal and (replaced.kind != "azure_change" or replaced.status != AWAITING_APPROVAL or replaced.limit != f"{subscription}/{group}"):
                refusal = f"`{replaced.id}` is not an open change in `{group}`, so it cannot be replaced. Draft without `replaces`."
            if refusal:
                return _say(refusal)
        raw = args.get("variables")
        variables = {} if code is not None else raw if isinstance(raw, dict) else {k: v for k, v in args.items() if k not in RESERVED}
        requester = user_scope.get_current_user_id() or ""
        change_id, purpose = records.new_id(), str(args.get("purpose") or "").strip()
        try:
            answer, commit = await asyncio.to_thread(records.ask_plan, self.agent, template=name, variables=variables, subscription=subscription,
                                                    resource_group=group, change_id=change_id, requester=requester, code=code)
            record = records.build_record(self.agent, answer, commit, change_id=change_id, requester=requester, template=name,
                                          variables=variables, subscription=subscription, resource_group=group, purpose=purpose, code=code)
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return _say(f"{error}\n\nNothing was drafted and there is no card. Tell the user why. Do not try to get the same result another way.")
        records.store_new(record, replaces=replaced)
        return self._offer(record, f"Change `{record.id}` drafted for {'free code' if code is not None else '`' + name + '`'} in `{group}`."
                                   + (f" It replaces `{replaced.id}`, which is superseded and its document void." if replaced else ""))

    async def _draft_set(self, args: dict[str, Any]) -> Response:
        requester = user_scope.get_current_user_id() or ""
        continues = str(args.get("continues") or "").strip()
        purpose = str(args.get("purpose") or "").strip()
        subscription = ""
        if str(args.get("subscription") or "").strip():  # a name or GUID; `continues` takes it from the record instead
            subscription, sub_problem = records.resolve_subscription(args.get("subscription"))
            if sub_problem:
                return _say(sub_problem)
        steps = args.get("steps")
        if continues:  # the rest of a set that stopped: its steps come from the record, never from the model
            old, refusal = self._mine({"change_id": continues})
            if not refusal and (not sets.is_set(old) or not (old.preflight.get("set") or {}).get("stopped_at")):
                refusal = f"`{old.id}` is not a change set that stopped part-way, so there is nothing to continue."
            if not refusal and steps:
                refusal = "`continues` takes the steps from the stopped set; do not give `steps`."
            if not refusal and subscription and subscription.lower() != sets.subscription_of(old).lower():
                refusal = "A continued set is in the subscription of the set it continues."
            if refusal:
                return _say(refusal)
            subscription, steps = sets.subscription_of(old), sets.remaining(old)
            purpose = purpose or f"Continues {old.id[:8]}: {old.preflight.get('purpose') or old.summary}"
        if problem := sets.steps_problem(subscription, steps):
            return _say(problem)
        replaced = None
        if str(args.get("replaces") or "").strip():  # a redraft of one still on a card: that card goes quiet once this one exists
            replaced, refusal = self._mine({"change_id": args["replaces"]})
            if not refusal and (replaced.kind != "azure_change" or replaced.status != AWAITING_APPROVAL or sets.subscription_of(replaced) != subscription):
                refusal = f"`{replaced.id}` is not an open change in this subscription, so it cannot be replaced. Draft without `replaces`."
            if refusal:
                return _say(refusal)
        try:
            record, dropped = await asyncio.to_thread(sets.draft, self.agent, subscription=subscription, steps=steps, purpose=purpose,
                                                      requester=requester, continues=continues)
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return _say(f"{error}\n\nNothing was drafted and there is no card. Tell the user why. Do not try to get the same result another way: "
                        "not as single changes, not as free code, not with another template.")
        records.store_new(record, replaces=replaced)
        return self._offer(record, f"Change set `{record.id}` drafted: {len(record.steps)} steps in {', '.join(dict.fromkeys(s['resource_group'] for s in record.steps))}."
                                   + (f" It replaces `{replaced.id}`, which is superseded and its document void." if replaced else "")
                                   + (f" Dropped, because Azure already matches: {'; '.join(dropped)}." if dropped else ""))

    def _offer(self, record: ChangeRecord, lead: str) -> Response:
        try:
            card.show(self.agent, record, card.change_card(record))
        except Exception as error:  # noqa: BLE001 - the card is the only way to approve; without it the record is dead
            record.status = CANCELLED
            change_store.save(record)
            return _say(f"{lead} The approval card could not be shown, so the change was cancelled: {error}")
        listing = "\n".join(f"  {line}" for line in (sets.plan_lines(record) if sets.is_set(record) else record.diff)[:30])
        return _say(f"{lead}\n\n{card._consequence(record)}\nTier: {record.tier}. Document {record.id[:8]}, digest {record.plan_digest[:16]}, "
                    f"valid until {card._day(record.expires_at)}.\nPlan:\n```\n{listing}\n```\n\n{STOP}")

    # --- resume ----------------------------------------------------------------------------------------------------

    async def _resume(self, args: dict[str, Any]) -> Response:
        record, refusal = self._mine(args)
        if refusal:
            return _say(refusal)
        if record.kind != "azure_change" or drift.is_accept(record) or adopt.is_adopt(record):
            return _say(f"`{record.id}` is not a template or free-code change, so it cannot be resumed. For drift, call drift_accept again; for adoption, call adopt again.")
        if record.status != AWAITING_APPROVAL:
            return _say(f"Change `{record.id}` is '{record.status}'; only a change awaiting approval can be resumed.")
        if record.expired():
            record.status = EXPIRED
            change_store.save(record)
            return _say(f"Change `{record.id}` expired with its document. Draft it again.")
        scope = records.workspace_of(record.limit)
        requester = user_scope.get_current_user_id() or ""
        if sets.is_set(record):
            return await self._resume_set(record, requester)
        code = records.code_of(record)
        if code is not None and (problem := records.free_code_problem(code, self.agent)):
            return _say(f"The plan could not be made again, so nothing was shown: {problem}")
        try:
            answer, commit = await asyncio.to_thread(records.ask_plan, self.agent, template=record.playbook_name, variables=record.variables,
                                                    subscription=scope["subscription"], resource_group=scope["resource_group"],
                                                    change_id=record.id, requester=requester, code=code)
            same = str(answer.get("plan_digest") or "") == record.plan_digest
            fresh = records.build_record(
                self.agent, answer, commit, change_id=record.id if same else records.new_id(), requester=requester, template=record.playbook_name,
                variables=record.variables, subscription=scope["subscription"], resource_group=scope["resource_group"],
                purpose=str(record.preflight.get("purpose") or record.summary),
                planned_note=f"Planned again {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}: same digest as the document" if same else "",
                code=code)
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return _say(f"The plan could not be made again, so nothing was shown: {error}")
        if same:
            record.preflight["checks"] = fresh.preflight["checks"]  # the card shows the new check; the hash does not cover it
            return self._offer(record, f"Change `{record.id}` plans to the same digest as its document.")
        records.store_new(fresh, replaces=record)
        return self._offer(fresh, f"The plan has changed since `{record.id}` was drafted, so that change is superseded and its document is "
                                  f"void. New change `{fresh.id}` with a new document.\n{records.differences(record.diff, fresh.diff)}")

    async def _resume_set(self, record: ChangeRecord, requester: str) -> Response:
        subscription = sets.subscription_of(record)
        steps = [{k: s[k] for k in ("template", "resource_group", "variables")} for s in record.steps]
        try:
            answer, commit = await asyncio.to_thread(sets.ask_plan_set, self.agent, subscription=subscription, steps=steps, change_id=record.id, requester=requester)
            same = str(answer.get("set_digest") or "") == record.plan_digest
            fresh = sets.build(self.agent, answer, commit, change_id=record.id if same else records.new_id(), requester=requester, subscription=subscription,
                               steps=steps, purpose=str(record.preflight.get("purpose") or record.summary), continues=str(record.preflight.get("continues") or ""),
                               planned_note=f"Planned again {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}: same digest as the document" if same else "")
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return _say(f"The plan could not be made again, so nothing was shown: {error}")
        if same:
            record.preflight["checks"] = fresh.preflight["checks"]  # the card shows the new check; the hash does not cover it
            return self._offer(record, f"Change set `{record.id}` plans to the same digest as its document.")
        records.store_new(fresh, replaces=record)
        return self._offer(fresh, f"The plan has changed since `{record.id}` was drafted, so that set is superseded and its document is void. "
                                  f"New change set `{fresh.id}` with a new document.\n{records.differences(sets.plan_lines(record), sets.plan_lines(fresh))}")

    # --- suggest ---------------------------------------------------------------------------------------------------

    def _suggest(self, args: dict[str, Any]) -> Response:
        """A removal or a stop, as a runbook for the engineer. This method and the code it calls never reach the executor."""
        from plugins._ea_azure.helpers import store

        kind = str(args.get("kind") or "").strip().lower()
        wanted = args.get("resources")
        wanted = [x.strip() for x in re.split(r"[,\n]", wanted) if x.strip()] if isinstance(wanted, str) else [str(x).strip() for x in wanted or []]
        if not _KIND.fullmatch(kind) or not wanted or len(wanted) > MAX_RESOURCES:
            return _say(f"A suggestion needs `kind` (a short name such as unattached_disk) and 1 to {MAX_RESOURCES} `resources` from the Azure inventory.")
        found = []
        for ref in wanted:
            try:
                row = store.get(ref)
            except store.Ambiguous:
                return _say(f"`{ref}` matches more than one stored resource. Use its full resource ID.")
            if not row:
                return _say(f"`{ref}` is not in the stored Azure inventory, and a suggestion is only written about what the inventory lists.")
            found.append(row)
        book = records.runbook(kind)
        names = ", ".join(r["name"] for r in found)
        finding = redact.redact_text(re.sub(r"\s+", " ", str(args.get("finding") or "")).strip()[:300])[0]
        record = ChangeRecord(
            requested_by=user_scope.get_current_user_id() or "", status=CLOSED, kind="azure_suggestion", playbook_name="suggestion",
            variables={"kind": kind}, limit=",".join(r["native_id"] for r in found), summary=f"{book['title']}: {names}"[:200],
            commands=[str(s).replace("{names}", names) for s in book["steps"]], render_method=FROM_REVIEWED_CLI, tier="suggestion",
            preflight={"kind": kind, "finding": finding, "purpose": finding or book["title"], "advice": [],
                       "checks": [{"label": "Resources are in the stored Azure inventory", "ok": True, "detail": names[:200]},
                                  {"label": "Nothing runs this: the engineer does it by hand", "ok": True, "detail": ""}]},
        )
        try:
            records.write_documents(record, "runbook")
            change_store.save(record)  # audited as azure.suggestion.written (kind, resource IDs)
            card.show(self.agent, record, card.suggestion_card(record))
        except Exception as error:  # noqa: BLE001
            return _say(f"The suggestion could not be written: {error}")
        return _say(f"Suggestion `{record.id}` written for {names}, with a runbook. You never delete or stop anything: this is for the engineer to do "
                    "by hand, outside the product, after their own approval. Tell them so and stop; do not look for another way to do it.")

    # --- status, list, cancel ----------------------------------------------------------------------------------------

    def _mine(self, args: dict[str, Any]) -> tuple[ChangeRecord | None, str]:
        change_id = str(args.get("change_id") or "").strip()
        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return None, str(error)
        if record.kind not in (*records.KINDS, "azure_suggestion"):
            return None, f"`{record.id}` is not an Azure change."
        return record, ""

    async def _status(self, args: dict[str, Any]) -> Response:
        record, refusal = self._mine(args)
        if refusal:
            return _say(refusal)
        try:
            settle = sets.settle if sets.is_set(record) else records.settle
            settled = await asyncio.to_thread(settle, self.agent, record, user_scope.get_current_user_id() or "") if record.status == "applying" else None
        except executor_client.ExecutorError as error:
            return _say(f"Change `{record.id}` is applying; its state could not be read now: {error}")
        if settled:
            return _say(settled)
        docs = ", ".join(f"{d.get('version')} {d.get('format')}{' (downloaded)' if d.get('downloaded_at') else ''}" for d in record.documents) or "none"
        return _say(f"Change `{record.id}` is **{record.status}**. {record.kind} `{record.playbook_name}` on `{record.limit}`.\n"
                    f"Approved by: {record.approved_by or 'nobody yet'}. Reference: {record.reference or 'none yet'}. "
                    f"Digest: {record.plan_digest[:16] or 'none'}. Documents: {docs}."
                    + "".join(f"\n  {st['n']}. {st['title']}: {st['status']}" for st in record.steps if sets.is_set(record)))

    def _list(self) -> Response:
        rows = [r for r in change_store.list_records() if r.kind in (*records.KINDS, "azure_suggestion")][:20]
        return _say("\n".join(f"- `{r.id}` **{r.status}** {r.kind} {r.playbook_name} on {r.limit}{' ' + r.reference if r.reference else ''}"
                              for r in rows) or "No Azure changes on record.")

    def _cancel(self, args: dict[str, Any]) -> Response:
        record, refusal = self._mine(args)
        if refusal:
            return _say(refusal)
        if record.kind not in records.KINDS or not record.is_open or record.status == "applying":
            return _say(f"Change `{record.id}` is '{record.status}' and cannot be cancelled.")
        record.status = CANCELLED
        change_store.save(record)
        return _say(f"Change `{record.id}` cancelled.")
