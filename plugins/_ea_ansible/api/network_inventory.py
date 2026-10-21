"""The executor's inventory and credential store, edited from here.

Gated on `plugins.manage` through `helpers/api_policy.py`, alongside
`ansible_config`, because both decide what the agent can reach: one chooses the
executor, this one chooses the devices.

The secret travels one way. A submitted credential goes to the executor and is
never stored here; the executor has no endpoint that gives one back, so neither
does this. What comes back is a fingerprint -- enough to tell whether the value
you are holding is the value that is loaded, and not enough to be it.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import admin_client


class NetworkInventory(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()
        try:
            if action == "get":
                return {
                    "ok": True,
                    **admin_client.get_inventory(),
                    "credentials": admin_client.list_credentials(),
                }

            if action == "save_inventory":
                document = input.get("inventory")
                if not isinstance(document, dict):
                    return {"ok": False, "error": "inventory must be an object"}
                return {"ok": True, **admin_client.put_inventory(None, document)}

            if action == "save_credential":
                name = str(input.get("name") or "").strip()
                secret = str(input.get("secret") or "")
                if not name:
                    return {"ok": False, "error": "a credential needs a name"}
                if not secret:
                    # Blank means "leave it alone", not "clear it", so the field
                    # can start empty on every load. Clearing is deleting.
                    return {"ok": False, "error": "no secret was submitted, so nothing changed"}
                return {
                    "ok": True,
                    "credential": admin_client.put_credential(
                        None,
                        name,
                        kind=str(input.get("kind") or "password"),
                        secret=secret,
                        username=str(input.get("username") or ""),
                    ),
                }

            if action == "delete_credential":
                name = str(input.get("name") or "").strip()
                if not name:
                    return {"ok": False, "error": "a credential needs a name"}
                return {"ok": True, **admin_client.delete_credential(None, name)}

            return {"ok": False, "error": f"Unknown action: {action}"}
        except admin_client.ExecutorError as error:
            return {"ok": False, "error": str(error)}
