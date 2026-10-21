"""Read and write the Azure connection settings and its credential. Gated on `plugins.manage` in
`helpers/api_policy.py`: this decides which tenant is read and holds the credential.

The log app (ADR 0063) is a second, certificate-only identity: `generate_logs_certificate`, `public_logs_certificate` and
`clear_logs_credential` work like the read credential's, and `save` takes the `logs` settings (client ID, workspaces,
tables, longest time range), checked by `logs.clean`. `read_approval` is saved here too, as one of three known values.

Neither the secret nor the key ever comes back. `get` reports the credential's kind, fingerprint and expiry; `save`
writes a submitted secret straight to `usr/.env` through `credential.set_secret`, and an empty one leaves what is
stored untouched, so the field can start blank on every load. A certificate's key is made and kept in `usr/.env`;
only its public half (`public_certificate`) can leave. Reads and saves work while the plugin is off.
"""

from __future__ import annotations

import logging
from typing import Any

from helpers.api import ApiHandler, Request, Response
from plugins._audit.helpers import audit
from plugins._ea_azure.helpers import config, credential, live, logs

EDITABLE = (
    "cloud", "tenant_id", "client_id", "auth", "secret_expires_on", "subscriptions_mode", "subscription_ids",
    "schedule", "keep_removed_days", "keep_history_days", "min_pull_interval_minutes", "read_approval", "logs",
)


def _view() -> dict[str, Any]:
    return {"ok": True, "enabled": config.is_enabled(), "config": {**config.get_config(), "logs": logs.settings()},
            "credential": credential.status(), "logs_credential": credential.logs_status()}


def _credential_set(status: dict) -> None:
    audit.emit("azure.credential_set", kind=status["kind"], fingerprint=status["fingerprint"], expires_on=status["expires_on"])


class AzureConfig(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()
        try:
            if action == "get":
                return _view()

            if action == "save":
                submitted = input.get("config")
                if not isinstance(submitted, dict):
                    return {"ok": False, "error": "config must be an object"}
                updates = {key: submitted[key] for key in EDITABLE if key in submitted}
                if "read_approval" in updates and updates["read_approval"] not in live.READ_APPROVAL:
                    return {"ok": False, "error": f"read_approval must be one of: {', '.join(live.READ_APPROVAL)}."}
                if "logs" in updates:
                    updates["logs"] = logs.clean(updates["logs"], strict=True)
                if "secret_expires_on" in input:
                    updates["secret_expires_on"] = input["secret_expires_on"]
                before = config.get_config()
                after = config.save(updates)
                before["logs"], after["logs"] = logs.clean(before.get("logs")), logs.clean(after.get("logs"))
                changed = [key for key in EDITABLE if before.get(key) != after.get(key)]
                if changed:
                    audit.emit("azure.settings_saved", changed=changed)
                secret = input.get("secret")
                if isinstance(secret, str) and secret.strip():
                    _credential_set(credential.set_secret(secret, after["secret_expires_on"]))
                return _view()

            if action == "generate_certificate":
                status = credential.generate_certificate()
                _credential_set(status)
                return {"ok": True, "credential": status, "public_certificate": credential.public_certificate().decode()}

            if action == "public_certificate":
                return {"ok": True, "public_certificate": credential.public_certificate().decode()}

            if action == "generate_logs_certificate":
                status = credential.generate_logs_certificate()
                audit.emit("azure.logs_credential_set", fingerprint=status["fingerprint"], expires_on=status["expires_on"])
                return {"ok": True, "logs_credential": status, "public_certificate": credential.public_logs_certificate().decode()}

            if action == "public_logs_certificate":
                return {"ok": True, "public_certificate": credential.public_logs_certificate().decode()}

            if action == "clear_logs_credential":
                credential.clear_logs()
                audit.emit("azure.logs_credential_cleared")
                return _view()

            if action == "clear_credential":
                credential.clear()
                audit.emit("azure.credential_cleared")
                return _view()

            return {"ok": False, "error": f"Unknown action: {action}"}
        except ValueError as exception:
            return {"ok": False, "error": str(exception)}
        except Exception:
            logging.getLogger(__name__).exception("azure_config failed")
            return {"ok": False, "error": "Azure settings could not be saved."}
