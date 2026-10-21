from helpers.api import ApiHandler, Request, Response
from helpers import dotenv
import models

API_KEY_PLACEHOLDER = "************"


class ApiKeys(ApiHandler):
    """Provider credentials: reading one back is an administrative act.

    `reveal` returns a key in plaintext and `set` writes one into `usr/.env`.
    Neither declared a permission, so both were reachable by every signed-in
    account -- including the `user` role, whose whole permission set is
    `chat.use`. A standard account could read every provider key the instance
    held while `settings_get`, which exists to protect exactly these values,
    correctly refused the same session with 403.

    `settings.manage` is the permission `helpers/rbac.py` already defines as
    covering "model providers, API keys, framework config", and
    `helpers/settings.py` masks these values even for administrators. This
    brings the endpoint in line with both.
    """

    @classmethod
    def required_permission(cls) -> str | None:
        from helpers import rbac

        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "get")  # get | set | reveal

        if action == "get":
            return self._get_keys()
        elif action == "set":
            return self._set_keys(input)
        elif action == "reveal":
            return self._reveal_key(input)

        return Response(status=400, response=f"Unknown action: {action}")

    def _get_keys(self) -> dict:
        from helpers.providers import get_providers

        providers = get_providers("chat") + get_providers("embedding")
        seen = set()
        keys = {}

        for p in providers:
            pid = p.get("value", "")
            if pid and pid not in seen:
                seen.add(pid)
                api_key = models.get_api_key(pid)
                has_key = bool(api_key and api_key.strip() and api_key != "None")
                keys[pid] = {
                    "label": p.get("label", pid),
                    "has_key": has_key,
                    "masked": API_KEY_PLACEHOLDER if has_key else "",
                }

        return {"keys": keys}

    def _set_keys(self, input: dict) -> dict:
        updates = input.get("keys", {})
        if not isinstance(updates, dict):
            return {"ok": False, "error": "Invalid keys format"}

        for provider, value in updates.items():
            if isinstance(value, str) and value != API_KEY_PLACEHOLDER:
                dotenv.save_dotenv_value(f"API_KEY_{provider.upper()}", value)

        return {"ok": True}

    def _reveal_key(self, input: dict) -> dict:
        provider = input.get("provider", "")
        if not provider:
            return {"ok": False, "error": "Missing provider"}
        api_key = models.get_api_key(provider)
        if api_key and api_key.strip() and api_key != "None":
            return {"ok": True, "value": api_key}
        return {"ok": True, "value": ""}
