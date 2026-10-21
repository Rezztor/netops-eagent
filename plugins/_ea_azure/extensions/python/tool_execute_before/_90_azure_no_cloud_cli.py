"""Refuse a model-written command that would reach Azure outside the product's own read-only identity.

The product talks to Azure only through the `azure_*` tools, which sign in with the read credential (Reader) or, for a
change, through the cloud executor's own identity behind the approval card, the CR or SR number, the change document and
the guard rails. An agent that installs a cloud CLI in the runner, starts a sign-in, or asks the engineer to enter a
device code would hold the engineer's own Azure rights (up to Owner) with a tool that can delete, stop and change
anything, with none of that in front of it. This gate fires on `tool_execute_before` and refuses such a call: it stops
the ordinary path (a cloud CLI install, `az login`, `Connect-AzAccount`, a device-code endpoint) and makes the attempt
visible in the audit.

A text match is not a sandbox: a determined script can hide the call (encode it, build the string at run time, fetch it).
This gate stops the plain attempt and records it; it is not containment.

ponytail: keyword match, the only control the application can apply to code it hands the runner. The real boundary is a
network egress fence on the runner container (no route to login.microsoftonline.com / management.azure.com), which the
runner, not this process, owns; add it there if it is not yet in place.
"""

from __future__ import annotations

import re
from typing import Any

from helpers.errors import RepairableException
from helpers.extension import Extension

#: Tools that run a command or a script the model wrote (the code runner and the terminal-input tool).
GATED_TOOLS = {"code_execution_tool", "input"}

#: Hostnames and the device-code marker: unambiguous, matched as substrings of the lower-cased text.
_HOSTS = (
    "login.microsoftonline.com", "login.microsoft.com", "login.windows.net", "management.azure.com",
    "management.core.windows.net", "graph.microsoft.com", "graph.windows.net", "devicecode", "device_code",
    "use-device-code", "use_device_code",
)
#: Package names and PowerShell cmdlets: they carry a hyphen or a dot, so a substring match is safe.
_PACKAGES = (
    "azure-cli", "azure-identity", "azure-mgmt", "azure-core", "azure.identity", "azure.mgmt", "azure.cli",
    "msal", "microsoft-authentication", "connect-azaccount", "az.accounts", "az login", "azd auth",
)
#: Azure's own command words, as a command: standing alone (not inside `lazy`, `jazz.py`) and followed by a
#: subcommand, so a variable named `az` (`az = 90`) is not a call to the CLI.
_TOKEN_RE = re.compile(r"(?<![a-z0-9_.\-])(az|azd|azcopy|bicep)\s+[a-z]")
#: General infrastructure tools: refused only when the same text names Azure, so Terraform for anything else still runs.
_IAC_RE = re.compile(r"(?<![a-z0-9_.\-])(terraform|tofu)(?![a-z0-9_\-])")
_AZURE_WORDS = ("azurerm", "azapi", "azuread", "azure")

REFUSAL = (
    "Azure is reached only through the azure_* tools, under the product's own read-only identity. "
    "Never install a cloud CLI, never start a sign-in, never ask the engineer to sign in or to enter a device code. "
    "If a tool is missing a value (such as a subscription), ask for that value by name or GUID; azure_change and the "
    "other azure_* tools accept a subscription by its name or its GUID from the stored inventory."
)


def _matched(text: str) -> str:
    """The first cloud-sign-in or cloud-CLI marker in `text` (lower-cased), or "". Hosts and package names by substring,
    the short command words only as whole tokens so a word that merely contains them does not trip it."""
    low = text.lower()
    for host in _HOSTS:
        if host in low:
            return host
    for pkg in _PACKAGES:
        if pkg in low:
            return pkg
    found = _TOKEN_RE.search(low)
    if found:
        return found.group(1)
    found = _IAC_RE.search(low)
    return found.group(1) if found and any(word in low for word in _AZURE_WORDS) else ""


def _text_of(args: dict[str, Any]) -> str:
    """Every string a model-written call carries (the code, a command, a script path, keystrokes), joined to scan once."""
    parts: list[str] = []
    for value in args.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(v for v in value if isinstance(v, str))
    return "\n".join(parts)


class AzureNoCloudCli(Extension):
    async def execute(self, tool_name: str = "", **kwargs) -> None:
        if tool_name not in GATED_TOOLS:
            return
        args = kwargs.get("tool_args") if isinstance(kwargs.get("tool_args"), dict) else {}
        marker = _matched(_text_of(args or {}))
        if not marker:
            return
        try:  # pragma: no cover - the audit plugin is bundled but can be switched off
            from plugins._audit.helpers import audit

            audit.emit("azure.cloud_cli_blocked", tool=tool_name, matched=marker)
        except Exception:  # noqa: BLE001 - a missing audit plugin must not turn the gate into an open door
            pass
        raise RepairableException(REFUSAL)
