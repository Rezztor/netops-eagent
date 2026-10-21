"""The Azure change gate. Nothing reaches the cloud executor through `azure_apply` (or `azure_action`) without it.

`_95_` for the reason `_95_change_gate` is: the attempt is audited at `_90_` first. It **fails closed**: an unreadable
record, an unresolvable actor or a check that raises refuses. The checks are this plugin's `conditions.CONDITIONS`, in
the build contract's order: a reviewed template, an engineer's approval, the plan unchanged, the approval in date, a CR or
SR number that matches the pattern as it is now, an issued document that was downloaded and is unchanged, and not applied
before (a change set also: its steps still hash to its digest, and it may be `applying`, to go on after a wait). Only the main agent may apply, and never unattended. Disabling the plugin removes the tools and this gate together.
"""

from __future__ import annotations

from typing import Any

from helpers import user_scope
from helpers.errors import RepairableException
from helpers.extension import Extension

GATED_TOOLS = {"azure_apply", "azure_action"}
#: The action of each gated tool that changes Azure. `close`, `draft`, `status` and `suggest` change nothing there.
GATED_ACTIONS = {"azure_apply": "apply", "azure_action": "apply"}


class AzureChangeGate(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        if tool_name not in GATED_TOOLS:
            return
        try:
            self._check(tool_name, kwargs)
        except RepairableException:
            raise
        except Exception as error:  # noqa: BLE001 - a broken gate that lets changes through is worse than none
            raise RepairableException(f"The Azure change gate could not complete its checks, so this change cannot be applied: {error}") from error

    def _check(self, tool_name: str, kwargs: dict[str, Any]) -> None:
        from plugins._ea_ansible.helpers import change_store
        from plugins._ea_ansible.helpers.change_model import APPLYING, APPROVED
        from plugins._ea_azure_change.helpers import conditions, records, sets

        args = (kwargs.get("tool_args") or {}) if isinstance(kwargs.get("tool_args"), dict) else {}
        if str(args.get("action") or ("apply" if tool_name == "azure_apply" else "draft")).strip().lower() != GATED_ACTIONS[tool_name]:
            return
        if self.agent is None:
            raise RepairableException("No agent context, so no actor. Refusing.")
        if getattr(self.agent, "number", 0) != 0:
            raise RepairableException("A subordinate agent cannot apply an Azure change. Ask the main agent to do it.")
        actor = user_scope.get_current_user_id()
        if not actor:
            raise RepairableException("This change has no identifiable actor, so it cannot be applied. An Azure change is never unattended.")
        change_id = str(args.get("change_id") or "").strip()
        if not change_id:
            raise RepairableException(f"{tool_name} takes a change_id and nothing else. Draft the change first.")
        record = change_store.load(change_id, actor)
        if record.status != APPROVED and not (sets.is_set(record) and record.status == APPLYING):  # a set goes on after a wait
            raise RepairableException(records.pass_on(f"Change {change_id} is '{record.status}', not 'approved'. An engineer must approve it, with a CR or SR number, before it can run."))
        refusal = conditions.first_failure(record, actor)
        if refusal:
            raise RepairableException(records.pass_on(refusal))
