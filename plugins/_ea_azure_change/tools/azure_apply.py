"""Apply an approved Azure change, and nothing else.

It takes a `change_id` and no other argument that can reach Azure: what runs is decided by a record an engineer approved
with a CR or SR number, after downloading its document. The gate (`_95_azure_change_gate.py`) has already refused the
call if any condition is unmet; the conditions run again here, at the moment of execution, so that the reference pattern
and the document files are checked as they are now.

The executor plans again and refuses to apply a plan whose digest differs from the approved one (the job is then
`superseded` and nothing ran). After an apply: the executor's own verify, an inventory pull of the tenant, the list of
what the product made, and the as-applied document (`records.finish`). There is no rollback action and no undo card
(ADR 0065, build contract D25): a back-out is steps in the document for the engineer.

A change set (`helpers/sets.py`, ADR 0073) is applied here too: `sets.run` applies its steps in order, each through the same executor
call, and stops at the first one that does not apply. A set may be `applying` (a wait ran out) and a later `apply` goes on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLYING, APPLIED, APPROVED, FAILED, VERIFY_FAILED
from plugins._ea_azure_change.helpers import conditions, executor_client, records, sets


def _say(text: str) -> Response:
    return Response(message=text, break_loop=False)


class AzureApply(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "apply").strip().lower()
        change_id = str(args.get("change_id") or "").strip()
        if not records.enabled():
            return _say("Azure changes are switched off on this instance.")
        if action not in ("apply", "close"):
            return _say(f"Unknown action '{action}'. Use apply or close. There is no rollback, delete or stop.")
        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return _say(str(error))
        if record.kind not in records.KINDS:
            return _say(f"`{record.id}` is not an Azure change, so this tool will not run it.")
        if action == "close":
            if record.status not in (APPLIED, VERIFY_FAILED, FAILED):
                return _say(f"Change `{record.id}` is '{record.status}'; only a finished change can be closed.")
            records.close_record(record)
            return _say(f"Change `{record.id}` closed.")
        return await self._apply(record)

    async def _apply(self, record: Any) -> Response:
        actor = user_scope.get_current_user_id() or ""
        if record.kind != "azure_change":
            return _say("A simple action is applied with azure_action, not here.")
        if not executor_client.is_configured(self.agent):
            return _say("Azure changes are not configured on this instance.")
        if record.status != APPROVED and not (sets.is_set(record) and record.status == APPLYING):
            return _say(records.pass_on(f"Change {record.id} is '{record.status}'. Only an approved change can be applied; for one that is applying, call azure_change status."))
        refusal = conditions.first_failure(record, actor)
        if refusal:
            return _say(records.pass_on(refusal))
        if sets.is_set(record):
            loop = asyncio.get_running_loop()
            progress = lambda text: asyncio.run_coroutine_threadsafe(self.set_progress(text), loop)  # noqa: E731
            return _say(await asyncio.to_thread(sets.run, self.agent, record, actor, progress))
        with records.in_flight(record.id) as mine:  # the settling loop leaves this change alone while this call waits on it
            if not mine:
                return _say(f"Change `{record.id}` is already being applied by another call. Check it later with azure_change status.")
            return await self._run(record, actor)

    async def _run(self, record: Any, actor: str) -> Response:
        scope = records.workspace_of(record.limit)
        if not change_store.take(record):  # one statement in the approval register: an approval runs once
            return _say(records.pass_on(f"Change {record.id} has no approval left to use: it was already applied, or is being applied. Nothing ran."))
        try:
            record.job_id = await asyncio.to_thread(
                executor_client.apply, self.agent, source=records.source_of(record), workspace=scope,
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
        return _say(records.finish(record, job, actor))
