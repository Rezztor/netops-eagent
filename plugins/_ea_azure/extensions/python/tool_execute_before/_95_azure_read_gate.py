"""The Azure read gate. Nothing reaches Azure through `azure_read` `run` without it.

`_95_` for the reason the change gate is (the attempt is audited at `_90_` first). It fails closed: an unreadable
record, an unresolvable actor or a check that raises refuses. The checks are `tools/azure_read.run_refusal`, the same
function the tool calls, so a record that is not approved, was changed after approval, has expired, was made by someone
else or was made against an older `reads.yaml` is refused here and again there. `request` only records and shows a
card, so it is not gated. Only the main agent may run a read, and never unattended.
"""

from __future__ import annotations

from typing import Any

from helpers import user_scope
from helpers.errors import RepairableException
from helpers.extension import Extension

GATED_TOOLS = {"azure_read"}


class AzureReadGate(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        if tool_name not in GATED_TOOLS:
            return
        try:
            self._check(kwargs)
        except RepairableException:
            raise
        except Exception as error:  # noqa: BLE001 - a broken gate that lets reads through is worse than none
            raise RepairableException(f"The Azure read gate could not complete its checks, so this read will not run: {error}") from error

    def _check(self, kwargs: dict[str, Any]) -> None:
        from plugins._ea_ansible.helpers import change_store
        from plugins._ea_azure.tools import azure_read

        args = (kwargs.get("tool_args") or {}) if isinstance(kwargs.get("tool_args"), dict) else {}
        if str(args.get("action") or "request").strip().lower() == "request":
            return
        if self.agent is None:
            raise RepairableException("No agent context, so no actor. Refusing.")
        if getattr(self.agent, "number", 0) != 0:
            raise RepairableException("A subordinate agent cannot run an Azure read. Ask the main agent to do it.")
        actor = user_scope.get_current_user_id()
        if not actor:
            raise RepairableException("This read has no identifiable actor, so it will not run. An Azure read is never unattended.")
        change_id = str(args.get("change_id") or "").strip()
        if not change_id:
            raise RepairableException("azure_read run takes a change_id and nothing else. Request the read first.")
        refusal = azure_read.run_refusal(change_store.load(change_id, actor), actor)
        if refusal:
            raise RepairableException(refusal)
