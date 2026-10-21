"""The appliance's networks, read-only, for the Networks view and the label beside a device.

Gated on `network.view` in `helpers/api_policy.py`. The list is the executor's reading of the
file the appliance console writes; nothing here changes a network (that is the console's menu
Network). An executor that cannot be asked, or a missing list, is an empty answer, not an error:
the screen then says nothing is known. `addresses` returns `labels` ("VLAN 200, routed") worked
out from the list; no device record stores one.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import executor_client, network_list


class NetworkList(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            listing = executor_client.networks(None)
        except executor_client.ExecutorError:
            listing = network_list.parse(None)
        addresses = input.get("addresses")
        labels = {a: network_list.label(a, listing) for a in addresses[:50] if isinstance(a, str)} if isinstance(addresses, list) else {}
        return {"ok": True, **listing, "hint": network_list.management_hint(listing), "labels": labels}
