"""Refresh device facts (vendor, model, serial, version, HA) through the executor in check mode.
Thin: the rules live in `helpers/facts/collect.py`. It only ever creates pending requests or
conflicts for an engineer to review. Gated on `inventory.manage` in `helpers/tool_permissions.py`."""

from __future__ import annotations

import asyncio
import json

from helpers.tool import Response, Tool

from plugins._ea_inventory.helpers import proposals


class InventoryRefreshFacts(Tool):
    async def execute(self, **kwargs) -> Response:
        from plugins._ea_inventory.helpers.facts import collect  # lazy: a fault here must not stop the tool loading

        names = self.args.get("devices")
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        try:
            out = await asyncio.to_thread(
                collect.refresh_facts, names or None, actor=proposals.current_actor(),
                dry_run=bool(self.args.get("dry_run")), agent=self.agent,
            )
        except Exception as error:  # noqa: BLE001 - reported to the agent, not raised
            return Response(message=f"inventory_refresh_facts failed: {error}", break_loop=False)
        c = out.counts
        done = (
            f"{'Dry run: ' if self.args.get('dry_run') else ''}{c['ok']} refreshed, {c['error']} failed, {c['skipped']} skipped; "
            f"{c['proposals']} pending requests, {c['conflicts']} conflicts, {c['unchanged']} values unchanged. "
            "These await an engineer in Inventory -> Pending changes and Conflicts; nothing on a device changed."
        )
        return Response(message=f"{done}\n\n{json.dumps({'batch_id': out.batch_id, 'results': out.results}, indent=1)}", break_loop=False)
