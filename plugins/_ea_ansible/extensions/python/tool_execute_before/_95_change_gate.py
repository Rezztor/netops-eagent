"""The change gate. Nothing reaches a device through `net_apply` without it.

`_95_`, not `_50_`. `plugins/_audit/.../_90_audit_tool_start.py` emits
`tool.start` at `_90_`; a gate that raises before that produces a blocked
network change with no audit record of the attempt. At `_95_` the attempt is
recorded first and the gate then emits its own verdict on both paths.

**It fails closed**, which inverts the local convention.
`plugins/_infection_check/helpers/checker.py` deliberately swallows its own
faults so a checker outage never blocks work. Here any error -- an unreadable
record, an unresolvable actor, a check that raises -- must refuse. A broken gate
that lets changes through is worse than no gate, because it looks like one.
"""

from __future__ import annotations

from typing import Any

from helpers import user_scope
from helpers.errors import RepairableException
from helpers.extension import Extension

GATED_TOOLS = {"net_apply", "net_plan"}  # a plan applies changes too (ADR 0053)


class NetworkChangeGate(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        if tool_name not in GATED_TOOLS:
            return

        try:
            self._check(tool_name, kwargs)
        except RepairableException:
            raise
        except Exception as error:  # noqa: BLE001 - see the module docstring
            raise RepairableException(
                "The network change gate could not complete its checks, so this "
                f"change cannot be applied: {error}"
            ) from error

    def _check(self, tool_name: str, kwargs: dict[str, Any]) -> None:
        from plugins._ea_ansible.helpers import change_store, conditions
        from plugins._ea_ansible.helpers.change_model import APPROVED

        agent = self.agent
        if agent is None:
            raise RepairableException("No agent context, so no actor. Refusing.")

        # A subordinate cannot apply. Nor can a scheduled run: `_run_task` does
        # not wrap in `user_scope`, so an unattended run has no actor and is
        # refused here by construction rather than by policy.
        if getattr(agent, "number", 0) != 0:
            raise RepairableException(
                "A subordinate agent cannot apply a network change. Ask the main "
                "agent to do it."
            )

        actor = user_scope.get_current_user_id()
        if not actor:
            raise RepairableException(
                "This change has no identifiable actor, so it cannot be applied. "
                "Network change is never unattended."
            )

        args = (kwargs.get("tool_args") or {}) if isinstance(kwargs.get("tool_args"), dict) else {}
        action = str(args.get("action") or "apply").strip().lower()
        if action != ("run" if tool_name == "net_plan" else "apply"):
            # `verify` and `close` change nothing on a device; `rollback` only draws
            # the undo's card. The undo itself is its own record, applied (and gated)
            # as `apply`.
            return

        change_id = str(args.get("change_id") or "").strip()
        if not change_id:
            raise RepairableException(
                f"{tool_name} takes a change_id and nothing else. Draft the change "
                "with net_change (or request the plan with net_plan) first."
            )

        try:
            record = change_store.load(change_id, actor)
        except change_store.ChangeNotFound as error:
            # Seen live: the model copied a 32-character id with one character wrong. Name what is waiting, so the
            # next call is the right one rather than a guess.
            waiting = [r for r in change_store.list_records(actor) if r.status == APPROVED][:3]
            listed = ", ".join(f"{r.id} ({r.playbook_name} on {r.limit})" for r in waiting)
            raise RepairableException(
                f"{error} Copy the id exactly as the card gave it. "
                + (f"Approved and waiting: {listed}." if waiting else "None of your changes is approved and waiting.")
            ) from error

        if record.status != APPROVED:
            raise RepairableException(
                f"Change {change_id} is '{record.status}', not 'approved'. "
                "An engineer must approve the exact commands before it can run."
            )

        refusal = conditions.first_failure(record, actor)
        if refusal:
            raise RepairableException(refusal)
