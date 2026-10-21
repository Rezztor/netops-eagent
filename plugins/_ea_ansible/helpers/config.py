"""Settings resolution for the network automation plugin.

Device credentials are not here and cannot be. They live on the executor, which
resolves them itself. This module resolves where the executor is, how hard to
lean on it, and where its two tokens are kept -- never a token's value.

Two keys, because there are two planes. `executor_token_key` names the job token
the agent carries; `admin_token_key` names the one that edits the inventory and
credentials, which nothing in the agent's path holds.
"""

from __future__ import annotations

import os
from typing import Any

from helpers import dotenv, plugins

PLUGIN_NAME = "_ea_ansible"

# Where the deployment says the executor is. Shipping the executor container is
# a deployment decision, and so is its address -- an operator who ran the
# `ansible` service has already said both, and should not have to say the second
# one again in a settings form. A value saved through the interface still wins,
# except the old default `http://ansible:8500`, which no longer resolves and is
# treated as unset.
ENV_EXECUTOR_URL = "REZZTOR_ANSIBLE_EXECUTOR_URL"

OLD_DEFAULT_URL = "http://ansible:8500"
DEFAULT_TOKEN_KEY = "REZZTOR_ANSIBLE_EXECUTOR_TOKEN"
DEFAULT_ADMIN_TOKEN_KEY = "REZZTOR_ANSIBLE_ADMIN_TOKEN"
DEFAULT_JOB_TIMEOUT = 300
DEFAULT_MAX_DEVICES = 10
#: How strictly a fixed read asks (ADR 0048). Free-form commands always ask.
READ_APPROVAL = ("every_time", "once_per_chat", "log_only")


def get_config(agent: Any | None = None) -> dict[str, Any]:
    cfg = plugins.get_plugin_config(PLUGIN_NAME, agent=agent) or {}
    saved_url = str(cfg.get("executor_url") or "")
    if saved_url.strip().rstrip("/") == OLD_DEFAULT_URL:
        saved_url = ""
    return {
        "executor_url": (
            (saved_url or os.getenv(ENV_EXECUTOR_URL) or "")
            .strip()
            .rstrip("/")
        ),
        "executor_token_key": str(
            cfg.get("executor_token_key") or DEFAULT_TOKEN_KEY
        ).strip(),
        "admin_token_key": str(
            cfg.get("admin_token_key") or DEFAULT_ADMIN_TOKEN_KEY
        ).strip(),
        "job_timeout_seconds": int(cfg.get("job_timeout_seconds") or DEFAULT_JOB_TIMEOUT),
        "max_devices_per_job": int(cfg.get("max_devices_per_job") or DEFAULT_MAX_DEVICES),
        # An unknown value asks every time: a typo must not loosen the gate.
        "read_approval": (
            str(cfg.get("read_approval") or "") if cfg.get("read_approval") in READ_APPROVAL else READ_APPROVAL[0]
        ),
        # ADR 0052. Only a real true turns it on; the gate refuses when it does.
        "require_local_model": cfg.get("require_local_model") is True,
    }


def executor_token(agent: Any | None = None) -> str:
    """The token, read from usr/.env at the moment it is needed.

    Never cached in the plugin's own config, never returned by an API handler,
    and never put in a log line or a tool response.
    """
    key = get_config(agent)["executor_token_key"]
    return str(dotenv.get_dotenv_value(key) or "").strip()


def is_configured(agent: Any | None = None) -> bool:
    """Both halves, because either alone is a broken instance rather than a
    configured one: an address with no token cannot authenticate, and a token
    with no address has nothing to authenticate to."""
    return bool(get_config(agent)["executor_url"] and executor_token(agent))


def public_config(agent: Any | None = None) -> dict[str, Any]:
    """What a settings screen may see. The token is reported as set or not set,
    never as itself."""
    cfg = get_config(agent)
    return {
        **{k: v for k, v in cfg.items() if k != "executor_token_key"},
        "executor_token_key": cfg["executor_token_key"],
        "executor_token_set": bool(executor_token(agent)),
        # Reported as set or not set, exactly like the job token. Which of the
        # two is missing is the useful part of the answer: an executor with a
        # job token and no administration token runs playbooks and cannot be
        # configured, which looks like a different fault than it is.
        "admin_token_set": bool(
            dotenv.get_dotenv_value(cfg["admin_token_key"]) or ""
        ),
        # Which source supplies each token, because the two can disagree and the
        # disagreement is invisible. usr/.env overrides the environment, so a
        # token once pasted into the form keeps winning after the deployment
        # rotates the Secret -- the instance authenticates with a value nobody
        # believes is still in use. A deployment that injects its own tokens
        # should be shown as the source, not offered a field that would shadow
        # it.
        "executor_token_origin": dotenv.value_origin(cfg["executor_token_key"]),
        "admin_token_origin": dotenv.value_origin(cfg["admin_token_key"]),
        "configured": is_configured(agent),
    }
