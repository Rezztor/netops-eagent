"""The `require_local_model` switch (ADR 0052): device data stays on a local model.

A model is local when its API base is a loopback or private address, or a bare
host name with no dot (a compose service such as `http://ollama:11434`). No API
base means a hosted provider's default endpoint, which is not local. The check
is on the literal host: a dotted name is never resolved, because a name that
resolves private today can be re-pointed, and refusing is the safe error.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

#: Tools whose result is device output or inventory data. `inventory_*` and `azure_*` are matched by prefix.
DEVICE_DATA_TOOLS = frozenset(
    {"net_read", "net_change", "net_apply", "net_plan", "net_credential", "solarwinds_lookup"}
)


def returns_device_data(tool_name: str) -> bool:
    name = str(tool_name or "").split(":")[0].strip()
    return name in DEVICE_DATA_TOOLS or name.startswith(("inventory_", "azure_"))


def is_local_base(api_base: str) -> bool:
    base = str(api_base or "").strip()
    if not base:
        return False
    try:
        host = (urlsplit(base if "://" in base else f"//{base}").hostname or "").rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host
    return ip.is_loopback or ip.is_private


def _effective_base(cfg: dict[str, Any]) -> str:
    # Same precedence as ModelConfig.build_kwargs: a kwargs api_base wins, even if empty.
    kwargs = cfg.get("kwargs") if isinstance(cfg.get("kwargs"), dict) else {}
    return str(kwargs["api_base"] if "api_base" in kwargs else cfg.get("api_base") or "")


def non_local_models(agent: Any | None) -> list[str]:
    """The chat and utility models, for this agent and the root agent its results flow up to, that are not local."""
    from plugins._model_config.helpers import model_config

    agents = [agent]
    root = getattr(getattr(agent, "context", None), "root_agent", None)
    if root is not None and root is not agent:
        agents.append(root)
    bad: list[str] = []
    for a in agents:
        for label, cfg in (
            ("chat model", model_config.get_chat_model_config(a)),
            ("utility model", model_config.get_utility_model_config(a)),
        ):
            who = f"the {label} ({cfg.get('provider') or '?'}/{cfg.get('name') or '?'})"
            if not is_local_base(_effective_base(cfg)) and who not in bad:
                bad.append(who)
    return bad


def refusal(tool_name: str, agent: Any | None) -> str | None:
    """Why this tool may not run now, or None. Raises nothing: a fault is the caller's refusal."""
    from plugins._ea_ansible.helpers import config

    if not config.get_config(agent)["require_local_model"] or not returns_device_data(tool_name):
        return None
    bad = non_local_models(agent)
    if not bad:
        return None
    return (
        f"{tool_name} returns device data and 'Keep device data on a local model' is on, but "
        f"{', '.join(bad)} is not local (its API base must be a loopback or private address, or a bare "
        "host name). Switch those models to a local endpoint in Settings -> Models, or ask an "
        "administrator to turn the switch off in Settings -> Network automation."
    )
