"""The configuration plane's client. The second module allowed to reach the executor.

`executor_client.py` runs jobs with the token the agent holds. This one edits the
inventory and the credentials with a token the agent does not have, and the
separation is the whole point: a compromised agent can run the reviewed playbooks
it was always allowed to run, and cannot rewrite the inventory that decides which
devices those playbooks reach.

E1 in `tests/test_net_contract.py` names exactly two modules. If a third appears,
the question to answer before adding it to that list is which token it carries
and why neither of these two could do the job.

Nothing here is reachable from a tool. The only callers are API handlers gated on
`plugins.manage`, so a path from the model to this module would have to go
through an endpoint the model cannot call.
"""

from __future__ import annotations

from typing import Any

from helpers import dotenv

from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers.executor_client import (
    ExecutorError,
    ExecutorRefused,
    ExecutorUnreachable,
    NotConfigured,
    _refusal,
    _send,
)

#: Where the administration token is kept, on the same terms as the job token:
#: in usr/.env, read at the moment of use, never cached in the plugin's config
#: and never returned by an API handler.
DEFAULT_ADMIN_TOKEN_KEY = "REZZTOR_ANSIBLE_ADMIN_TOKEN"

__all__ = [
    "ExecutorError",
    "ExecutorRefused",
    "ExecutorUnreachable",
    "NotConfigured",
    "admin_token",
    "admin_token_key",
    "delete_credential",
    "get_inventory",
    "list_credentials",
    "put_credential",
    "put_inventory",
]


def admin_token_key(agent: Any | None = None) -> str:
    cfg = plugin_config.get_config(agent)
    return str(cfg.get("admin_token_key") or DEFAULT_ADMIN_TOKEN_KEY).strip()


def admin_token(agent: Any | None = None) -> str:
    """Read from usr/.env at the moment it is needed. Never logged, never returned."""
    return str(dotenv.get_dotenv_value(admin_token_key(agent)) or "").strip()


def _headers(agent: Any | None) -> dict[str, str]:
    token = admin_token(agent)
    if not token:
        raise NotConfigured(
            "No administration token is set for the Ansible executor, so its "
            "inventory and credentials cannot be changed from here. Paste it "
            "in Settings → Network automation. It is not the job token."
        )
    return {"Authorization": f"Bearer {token}"}


def _call(agent: Any | None, method: str, path: str, payload: Any = None) -> dict[str, Any]:
    response = _send(agent, method, path, headers=_headers(agent), json=payload)
    if response.status_code >= 400:
        raise ExecutorRefused(_refusal(response))
    return response.json() if response.content else {}


# --- inventory ---------------------------------------------------------------

def get_inventory(agent: Any | None = None) -> dict[str, Any]:
    return _call(agent, "GET", "/inventory")


def put_inventory(agent: Any | None, document: dict[str, Any]) -> dict[str, Any]:
    return _call(agent, "PUT", "/inventory", {"inventory": document})


# --- credentials -------------------------------------------------------------

def list_credentials(agent: Any | None = None) -> list[dict[str, Any]]:
    """Names, kinds and fingerprints. There is no call that returns a secret,
    here or at the other end."""
    return list(_call(agent, "GET", "/credentials").get("credentials") or [])


def put_credential(
    agent: Any | None, name: str, *, kind: str, secret: str, username: str = ""
) -> dict[str, Any]:
    return _call(
        agent,
        "PUT",
        f"/credentials/{name}",
        {"kind": kind, "secret": secret, "username": username},
    )


def delete_credential(agent: Any | None, name: str) -> dict[str, Any]:
    return _call(agent, "DELETE", f"/credentials/{name}")
