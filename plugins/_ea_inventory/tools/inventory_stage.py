"""Stage inventory records from a file the engineer attached. Thin: the rules live in
`helpers/staging.py`. It re-reads the redacted source itself and only ever creates
pending requests; nothing changes a device until a person approves. Gated on
`inventory.manage` in `helpers/tool_permissions.py`."""

from __future__ import annotations

import asyncio
import json

from helpers.tool import Response, Tool

from plugins._ea_inventory.helpers import proposals


class InventoryStage(Tool):
    async def execute(self, **kwargs) -> Response:
        # Lazy: a missing parser library must not stop the tool from loading.
        from plugins._ea_inventory.helpers import readers, redact, staging

        file = str(self.args.get("file") or "").strip()
        note = str(self.args.get("note") or "").strip()
        try:
            doc = await asyncio.to_thread(readers.read_source, file)
            result = staging.stage(
                doc, self.args.get("records"), file, proposals.current_actor(),
                redact.redact_text(note)[0] if note else "",
            )
        except Exception as error:  # noqa: BLE001 - reported to the agent, not raised
            return Response(message=f"inventory_stage failed: {error}", break_loop=False)
        done = (
            f"Staged batch {result['batch_id']}: {result['created']} to add, {result['updates']} to update, "
            f"{result['unchanged']} unchanged, {len(result['rejected'])} rejected. These are pending "
            "requests, not devices: tell the engineer to review them in Inventory -> Pending changes."
        )
        return Response(message=f"{done}\n\n{json.dumps(result, indent=1)}", break_loop=False)
