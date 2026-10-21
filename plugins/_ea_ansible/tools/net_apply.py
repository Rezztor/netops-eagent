"""Apply an approved change, and nothing else.

It takes a `change_id` and no other argument that can reach a device. There is
no field here for a command, a playbook, a device address or a credential --
what runs is entirely determined by a record an engineer already approved.

The gate at `extensions/python/tool_execute_before/_95_change_gate.py` has
already refused this call if any condition is unmet, and it fails closed. The
checks repeated here are not belt and braces for their own sake: the gate runs
before the tool, and re-reading the record at the moment of execution is what
makes condition 3 -- the plan still renders to what was approved -- true rather
than merely checked earlier.
"""

from __future__ import annotations

import re
import time
from typing import Any

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import catalogue, change_store, conditions, render
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import executor_client
from plugins._ea_ansible.helpers.change_model import (
    APPLIED,
    APPROVED,
    AWAITING_APPROVAL,
    CLOSED,
    FAILED,
    FROM_REVIEWED_CLI,
    ROLLED_BACK,
    SUPERSEDED,
    VERIFY_FAILED,
    ChangeRecord,
)
from plugins._ea_ansible.tools.net_change import EXPIRY_SECONDS, show_card
from plugins._ea_inventory.helpers import redact

#: What a change can be rolled back from: it reached a device, whatever happened next.
APPLIED_STATES = (APPLIED, FAILED, VERIFY_FAILED)


class NetApply(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "apply").strip().lower()
        change_id = str(args.get("change_id") or "").strip()

        if not plugin_config.is_configured(self.agent):
            return Response(
                message="Network automation is not configured on this instance.",
                break_loop=False,
            )

        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return Response(message=str(error), break_loop=False)

        if action == "verify":
            return self._verify(record)

        if action == "rollback":
            return self._rollback(record)

        if action == "close":
            record.status = CLOSED
            change_store.save(record)
            return Response(message=f"Change `{record.id}` closed.", break_loop=False)

        if action != "apply":
            return Response(
                message=(
                    f"Unknown action '{action}'. Use apply, verify, rollback or close."
                ),
                break_loop=False,
            )

        if record.kind == "read":
            return Response(
                message=f"`{record.id}` is a read, not a change. Reads run through net_read.",
                break_loop=False,
            )

        if record.status != APPROVED:
            return Response(
                message=(
                    f"Change {record.id} is '{record.status}'. Only an approved change "
                    "can be applied."
                ),
                break_loop=False,
            )

        # Condition 3, at the moment it matters. The engineer approved a specific
        # text; if the devices have moved since, what would run is not what was
        # read, and the record is superseded rather than quietly applied.
        try:
            entries = catalogue.load()
            entry = catalogue.resolve(record.playbook_name, entries)
            fresh = render.render(
                self.agent, entry, record.variables, record.limit, record.commit
            )
        except (catalogue.CatalogueError, catalogue.UnknownEntry, render.RenderFailed,
                executor_client.ExecutorError) as error:
            return Response(
                message=f"The plan could not be re-checked, so it will not be applied: {error}",
                break_loop=False,
            )

        record.commands = fresh["commands"]
        record.diff = fresh["diff"]
        refusal = conditions.first_failure(record, user_scope.get_current_user_id())
        if refusal:
            if record.approved_hash and record.approved_hash != record.current_hash:
                record.status = SUPERSEDED
                change_store.save(record)
            return Response(message=refusal, break_loop=False)

        # The approval is used here, in one statement in the register: of two applies one proceeds, and a record
        # file put back after its apply finds nothing left to use.
        if not change_store.take(record):
            return Response(
                message=f"Change {record.id} has no approval left to use: it was already applied, or is being applied. Nothing ran.",
                break_loop=False,
            )

        try:
            result = executor_client.run(
                self.agent,
                playbook_name=record.playbook_name,
                commit=record.commit,
                variables=record.variables,
                limit=record.limit,
                check_mode=False,
            )
        except executor_client.ExecutorError as error:
            record.status = FAILED
            record.error = str(error)
            change_store.save(record)
            if isinstance(error, executor_client.JobStillRunning) and record.kind != "rollback":
                # Sent and not finished: it may still reach the devices, so say so and offer the way back.
                return Response(
                    message=(
                        f"Change `{record.id}` was sent, but its outcome is **unknown**: {error} It may still be "
                        "applied. Tell the engineer, and check with net_apply action=verify before anything else."
                        f"\n\n{self._offer_rollback(record)}"
                    ),
                    break_loop=False,
                )
            return Response(message=f"The change did not run: {error}", break_loop=False)

        record.exit_code, record.job_id = result.exit_code, result.job_id
        record.applied_at = time.time()
        proof = self._prove(record, entry) if result.succeeded else ""
        record.status = (
            FAILED if not result.succeeded else VERIFY_FAILED if record.verified is False else APPLIED
        )
        if not result.succeeded:
            record.error = f"Ansible exited with code {result.exit_code}."
        change_store.save(record)

        if record.kind == "rollback":
            return self._after_rollback(record, result, proof)

        offer = self._offer_rollback(record)
        if not result.succeeded:
            return Response(
                message=(
                    f"Change `{record.id}` **failed** (exit code {result.exit_code}).\n\n"
                    f"```\n{result.output[-3000:]}\n```\n\n{offer}"
                ),
                break_loop=False,
            )

        return Response(
            message=(
                f"Change `{record.id}` **{'applied' if record.verified is not False else 'applied, but did not verify'}**"
                f" to `{record.limit}`.\n\n```\n{result.output[-4000:]}\n```\n\n{proof}\n\n{offer}"
            ),
            break_loop=False,
        )

    def _prove(self, record: ChangeRecord, entry: Any) -> str:
        """Run the entry's `verify` through `reads/show`, one device at a time.

        Sets `record.verified` (True, False, or None when the entry declares none or no
        device's platform has commands) and a redacted excerpt. Never raises: a verify
        that cannot run is a failed verify, because "nobody checked" must not read as
        "checked and fine". The commands are shape-checked at catalogue load.
        """
        spec = entry.verify
        record.verified = None
        if not spec:
            return "This entry declares no verify, so nothing was checked after the apply."
        want = re.compile(render.fill(spec["expect"], record.variables, re.escape))
        results, excerpts = [], []
        for device in record.devices_resolved:
            commands = spec["commands"].get(device.get("platform"))
            if not commands:
                continue
            try:
                run = executor_client.run(
                    self.agent,
                    playbook_name="reads/show",
                    commit=record.commit,
                    variables={f"command_{i}": c for i, c in enumerate(commands, 1)},
                    limit=device["name"],
                    check_mode=True,
                )
                ok = run.succeeded and bool(want.search(run.output)) != bool(spec.get("absent"))
                text = redact.redact_text(run.output[-1500:])[0]
            except executor_client.ExecutorError as error:
                ok, text = False, f"The verify did not run: {error}"
            results.append(ok)
            excerpts.append(f"# {device['name']}: {'ok' if ok else 'FAILED'}\n{text}")
        record.verified = all(results) if results else None
        record.output_excerpt = "\n".join(excerpts)[-3000:]
        if record.verified is None:
            return "No verify commands for these devices' platforms, so nothing was checked after the apply."
        return ("**Verified**" if record.verified else "**Verify FAILED**") + f":\n```\n{record.output_excerpt}\n```"

    def _offer_rollback(self, change: ChangeRecord) -> str:
        """Save a `kind="rollback"` record for `change` and put its card on screen.

        The undo is approved like any change: its own record, hash, expiry and card,
        run through the same gate by `apply`. The commands are the ones the approver of
        the change already read (`change.rollback_commands`).
        """
        if not change.rollback_name:
            return "This change names no rollback, so there is no reviewed way back; it has to be undone by hand."
        try:
            entry, variables, commands = render.rollback_plan(
                catalogue.load(), change.rollback_name, change.variables
            )
        except (catalogue.CatalogueError, catalogue.UnknownEntry, catalogue.VariableRejected) as error:
            return f"A rollback could not be offered: {error}. The change has to be undone by hand."
        undo = ChangeRecord(
            kind="rollback",
            requested_by=change.requested_by,
            status=AWAITING_APPROVAL,
            playbook_name=entry.name,
            commit=change.commit,
            variables=variables,
            limit=change.limit,
            summary=f"Undo {change.playbook_name}",
            commands=change.rollback_commands or commands,
            render_method=FROM_REVIEWED_CLI,
            expires_at=time.time() + EXPIRY_SECONDS,
            devices_resolved=change.devices_resolved,
            tier=change.tier,
            confirm=change.confirm,
            rollback_of=change.id,
        )
        change_store.save(undo)
        change.rollback_id = undo.id
        change_store.save(change)
        show_card(
            self.agent, undo, f"Roll back `{change.playbook_name}` on {change.limit}?",
            f"Undoes change {change.id}. Approving binds to these commands as they stand now.",
        )
        return (
            f"A 'Roll back?' card for it is on screen (rollback `{undo.id}`). Do not run it unless the "
            f"engineer approves it; then call net_apply with change_id={undo.id} and no action "
            "(apply is the default; do not pass action=rollback for it)."
        )

    def _after_rollback(self, undo: ChangeRecord, result: Any, proof: str) -> Response:
        if result.succeeded and undo.verified is not False:
            original = change_store.load(undo.rollback_of)
            original.status = ROLLED_BACK  # audited as network.change.rolled_back
            change_store.save(original)
            verb = "rolled back"
        else:
            verb = "**did not roll back cleanly**"
        return Response(
            message=(
                f"Change `{undo.rollback_of}` {verb} on `{undo.limit}`.\n\n"
                f"```\n{result.output[-3000:]}\n```\n\n{proof}"
            ),
            break_loop=False,
        )

    # --- after the change ----------------------------------------------------

    def _verify(self, record: Any) -> Response:
        """Ask the devices whether they are in the state the change intended.

        Re-runs the *change* entry in check mode. A module that reports no
        commands has nothing left to do, which is the same thing as the device
        already being in the intended state -- and it is a stronger answer than
        re-reading the config and comparing strings, because the module's own
        idea of "already correct" is what decided there was a change to make.

        Deliberately available after a failure as well as after a success. "The
        play exited non-zero" and "nothing reached the device" are different
        situations, and a partial apply is the one where knowing which matters
        most.
        """
        if record.status not in APPLIED_STATES:
            return Response(
                message=(
                    f"Change {record.id} is '{record.status}'. There is nothing to "
                    "verify until it has been applied."
                ),
                break_loop=False,
            )

        try:
            entries = catalogue.load()
            entry = catalogue.resolve(record.playbook_name, entries)
            fresh = render.render(
                self.agent, entry, record.variables, record.limit, record.commit
            )
        except (catalogue.CatalogueError, catalogue.UnknownEntry, render.RenderFailed,
                executor_client.ExecutorError) as error:
            return Response(
                message=f"The devices could not be re-checked: {error}",
                break_loop=False,
            )

        outstanding = list(fresh["commands"])
        if not outstanding:
            return Response(
                message=(
                    f"Verified: the devices in `{record.limit}` report nothing left to "
                    f"do for change `{record.id}`."
                ),
                break_loop=False,
            )

        listing = "\n".join(f"  {line}" for line in outstanding)
        return Response(
            message=(
                f"**Change `{record.id}` is not fully in place.** The devices still "
                f"report work outstanding:\n```\n{listing}\n```\n"
                "That is either a partial apply or drift since. Re-applying needs a "
                "fresh approval, because these are not the commands anyone read."
            ),
            break_loop=False,
        )

    def _rollback(self, record: Any) -> Response:
        """Offer the undo of an applied change as a card; nothing reaches a device here.

        The undo runs only when its own record is approved and applied, through the
        same gate as the change (hash, expiry, requester, audit). So this action is
        not gated: it can only draw a card.
        """
        if record.kind == "rollback":
            # Seen on the first live rollback: the agent passed the undo's own id here and was
            # told there was nothing to roll back. It must go through `apply`, because that is
            # the action the gate checks; turning this call into an apply would step around it.
            return Response(
                message=(
                    f"`{record.id}` is the rollback itself, not the change it undoes. Once the engineer "
                    f"has approved its card, run it with net_apply, change_id={record.id}, and no "
                    "action (the default, apply). It passes the same gate as any change."
                ),
                break_loop=False,
            )
        if record.status not in APPLIED_STATES:
            return Response(
                message=(
                    f"Change {record.id} is '{record.status}'. There is nothing "
                    "applied to roll back."
                ),
                break_loop=False,
            )
        return Response(message=self._offer_rollback(record), break_loop=False)
