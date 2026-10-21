"""A Restricted installation starts no new device or cloud change, also from a run that was already going.

New runs are refused where they start (`helpers.license_entitlements.require_agent_runs_allowed`). A run that
was in flight when the licence state changed is not killed; it is refused the tools that draft or apply a
change. Reads stay available.
"""

from helpers.errors import RepairableException
from helpers.extension import Extension
from helpers.license_entitlements import RESTRICTED_TOOLS, restricted_refusal


class LicenceRestricted(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        if tool_name not in RESTRICTED_TOOLS:
            return
        refusal = restricted_refusal()
        if refusal:
            raise RepairableException(refusal)
