"""Refuse a device-data tool unless every model its result could reach is local (ADR 0052).

Fails closed like `_95_change_gate`: a check that raises is a refusal, because
a switch that silently stops working would leak the data it promises to keep.
Applies only when `require_local_model` is on.
"""

from __future__ import annotations

from helpers.errors import RepairableException
from helpers.extension import Extension


class LocalModelGate(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        from plugins._ea_ansible.helpers import local_model

        try:
            reason = local_model.refusal(tool_name, self.agent)
        except Exception as error:  # noqa: BLE001 - fail closed, see the docstring
            raise RepairableException(
                f"Could not check whether the model is local, so {tool_name} was not run: {error}"
            ) from error
        if reason:
            raise RepairableException(reason)
