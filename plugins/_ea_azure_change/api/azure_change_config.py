"""Guard rails, keys and the role check of the Azure change engine: the Settings screen's endpoint. `plugins.manage`.

A proxy to the cloud executor's `/v1/admin/*` with the admin token, which never leaves `executor_client`. Nothing that
looks like a key, token, secret or password is returned: `make_key` returns the public certificate, its thumbprint and
its expiry, and nothing else. The guard rails have a fixed set of names (`RAILS`); there is no `deletes_allowed` or
`max_deletes` and none can be added here (ADR 0065). Saving them writes the executor first and, only when it accepted,
the copy the application reads (`reference_pattern`, `document_valid_days`, the cost limits) into this plugin's config. A
`reference_pattern` that does not compile, matches the empty string or is longer than 100 characters is refused.
Audited: `azure.guardrails_saved` (changed keys, never values), `azure.key_made`, `azure.role_checked`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from helpers import plugins
from helpers.api import ApiHandler, Request, Response

from plugins._audit.helpers import audit
from plugins._ea_azure_change.helpers import executor_client, records

RAILS = ("subscriptions", "resource_groups", "regions", "resource_types", "vm_sizes_max_vcpu", "usd_month_per_change",
         "usd_month_total", "low_risk_usd_month", "max_resources", "allow_free_code", "allow_unknown_price", "reference_pattern",
         "document_valid_days")
ROLES = ("planner", "applier")
_SECRET = re.compile(r"(?i)token|secret|password|private|passphrase|key_pem|pfx")


def scrub(value: Any) -> Any:
    """Drop anything key-like from an executor answer before it goes to a browser (a flag such as `passphrase_set` stays)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if isinstance(v, bool) or not _SECRET.search(str(k))}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def rails_problem(rails: Any) -> str | None:
    if not isinstance(rails, dict):
        return "guardrails must be an object."
    unknown = sorted(set(rails) - set(RAILS))
    if unknown:
        return f"Unknown guard rail {', '.join(unknown)}. There is no setting that allows a delete, a stop or a restart."
    if "reference_pattern" in rails and (problem := records.pattern_problem(rails["reference_pattern"])):
        return problem
    if "document_valid_days" in rails and not (isinstance(rails["document_valid_days"], int) and 1 <= rails["document_valid_days"] <= 30):
        return "document_valid_days must be a whole number from 1 to 30."
    return None


class AzureChangeConfig(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()
        try:
            return await asyncio.to_thread(self._run, action, input)
        except executor_client.ExecutorError as error:
            return {"ok": False, "error": str(error), "local": records.rails()}
        except ValueError as error:
            return {"ok": False, "error": str(error)}

    def _run(self, action: str, input: dict) -> dict[str, Any]:
        admin = executor_client.admin
        if action == "get":
            return {"ok": True, "enabled": records.enabled(), "configured": executor_client.is_configured(),
                    "guardrails": scrub(admin(None, "GET", "/v1/admin/guardrails")), "local": records.rails()}
        if action == "keys":
            return {"ok": True, **scrub(admin(None, "GET", "/v1/admin/keys"))}
        if action == "test":
            answer = scrub(admin(None, "POST", "/v1/admin/test"))
            audit.emit("azure.role_checked", role_ok=bool(answer.get("role_ok")), reason=str(answer.get("reason") or "")[:300])
            return {"ok": True, **answer}
        if action == "make_key":
            role = str(input.get("role") or "")
            if role not in ROLES:
                return {"ok": False, "error": f"role must be one of {ROLES}."}
            body = {"tenant_id": str(input.get("tenant_id") or "").strip(), "client_id": str(input.get("client_id") or "").strip()}
            if not all(body.values()):
                return {"ok": False, "error": "tenant_id and client_id are needed."}
            made = admin(None, "POST", f"/v1/admin/keys/{role}", body)
            out = {k: made.get(k) for k in ("ok", "public_certificate", "thumbprint", "expires_on")}
            audit.emit("azure.key_made", role=role, thumbprint=out["thumbprint"], expires_on=out["expires_on"])
            return out
        if action == "save_guardrails":
            submitted = input.get("guardrails")
            problem = rails_problem(submitted)
            if problem:
                return {"ok": False, "error": problem}
            before = scrub(admin(None, "GET", "/v1/admin/guardrails"))
            stored = scrub(admin(None, "PUT", "/v1/admin/guardrails", {**before, **submitted}))
            self._save_local({**records.rails(), **{k: v for k, v in stored.items() if k in RAILS}})
            audit.emit("azure.guardrails_saved", changed=sorted(k for k in submitted if before.get(k) != stored.get(k)))
            return {"ok": True, "guardrails": stored, "local": records.rails()}
        return {"ok": False, "error": f"Unknown action: {action}"}

    @staticmethod
    def _save_local(rails: dict[str, Any]) -> None:
        """What the application itself reads. Defaults are carried over: a saved config replaces the shipped one whole."""
        name = records.PLUGIN_NAME
        merged = {**(plugins.get_default_plugin_config(name) or {}), **(plugins.get_plugin_config(name) or {}), "guardrails": rails}
        plugins.save_plugin_config(name, project_name="", agent_profile="", settings=merged)
