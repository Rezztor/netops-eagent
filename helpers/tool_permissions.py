"""Which role may invoke which agent tool.

helpers/rbac.py declares permissions like tools.code_execution, and the Roles
screen tells an administrator that a standard user cannot execute code. Until
this module existed that statement was not true: the permissions were declared
and never checked, so every role could invoke every tool.

Enforcement happens where a tool is resolved by name, in Agent.get_tool, rather
than inside each tool. A tool that forgets to check is the whole problem; a
resolver that refuses cannot be bypassed by adding a new tool.

The prompt still describes tools the user may not run. That is deliberate: the
alternative is a per-role system prompt, and a refusal the model can read and
explain is more useful to the person than a tool that silently does not exist.
"""

from __future__ import annotations

import re

from helpers import rbac

_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]+")

# Tool name -> permission required to invoke it.
#
# Tools absent from this map need no permission beyond being signed in. That is
# the safe default here because the dangerous capabilities are enumerable and
# few, while the harmless ones (respond, wait, memory recall) are many and
# change often.
TOOL_PERMISSIONS: dict[str, str] = {
    # Arbitrary code and shell, in the container and on a connected host.
    "code_execution_tool": rbac.PERM_TOOL_CODE_EXECUTION,
    "code_execution_remote": rbac.PERM_TOOL_CODE_EXECUTION,
    "input": rbac.PERM_TOOL_CODE_EXECUTION,
    "text_editor": rbac.PERM_TOOL_CODE_EXECUTION,
    "text_editor_remote": rbac.PERM_TOOL_CODE_EXECUTION,
    "office_artifact": rbac.PERM_TOOL_CODE_EXECUTION,
    # Fetches a file from the runner: claims the account a slot and runs commands there as it.
    "attach_file": rbac.PERM_TOOL_CODE_EXECUTION,
    # Spawns further agents, multiplying whatever the caller can already do.
    "call_subordinate": rbac.PERM_TOOL_SUBORDINATES,
    "parallel": rbac.PERM_TOOL_SUBORDINATES,
    # Reach network devices through the Ansible executor. The application never
    # connects to a device; what may actually run is decided by the change gate,
    # not by this permission.
    "net_read": rbac.PERM_TOOL_NETWORK,
    "net_change": rbac.PERM_TOOL_NETWORK,
    "net_apply": rbac.PERM_TOOL_NETWORK,
    "net_plan": rbac.PERM_TOOL_NETWORK,
    "net_credential": rbac.PERM_TOOL_NETWORK,
    # Live infrastructure read from SolarWinds, archived running configurations included.
    "solarwinds_lookup": rbac.PERM_TOOL_NETWORK,
    # Read-only, but it is the estate: the same people who may open the screen.
    "inventory_lookup": rbac.PERM_INVENTORY_VIEW,
    "inventory_ports": rbac.PERM_INVENTORY_VIEW,
    # Stages proposals from a file; a person still approves them.
    "inventory_stage": rbac.PERM_INVENTORY_MANAGE,
    "inventory_read_file": rbac.PERM_INVENTORY_MANAGE,
    "inventory_refresh_facts": rbac.PERM_INVENTORY_MANAGE,
    # The Azure snapshot: reading is the estate's own permission; a pull asks Azure again, so it is `manage`.
    "azure_inventory": rbac.PERM_INVENTORY_VIEW,
    "azure_advise": rbac.PERM_INVENTORY_VIEW,
    "azure_cost": rbac.PERM_INVENTORY_VIEW,
    "azure_pull": rbac.PERM_INVENTORY_MANAGE,
    # Azure changes and live reads: drafting is the engineer's own work; what may run is the plugin's gate
    # (a CR/SR number, a document, the engineer's approval), never this permission.
    "azure_change": rbac.PERM_TOOL_NETWORK,
    "azure_apply": rbac.PERM_TOOL_NETWORK,
    "azure_action": rbac.PERM_TOOL_NETWORK,
    "azure_read": rbac.PERM_TOOL_NETWORK,
    # Runs unattended, later, without anyone watching.
    "scheduler": rbac.PERM_SCHEDULER_MANAGE,
}


def required_permission(tool_name: str) -> str | None:
    return TOOL_PERMISSIONS.get((tool_name or "").strip())


def is_tool_name(tool_name: str) -> bool:
    """True for a name that can only be a tool's file name: letters, digits, `_` and `-`, nothing else."""
    return bool(_TOOL_NAME.fullmatch(tool_name or ""))


def is_allowed(tool_name: str, role: str | None) -> bool:
    # A tool is found by file name, so a name with a path in it ("./code_execution_tool") would load the real tool
    # while matching no entry above, and no gate that compares names -- and Agent.get_tool asks this before it looks
    # on disk, so a name that is a path to any file must never get further. Every tool file name is a plain
    # identifier: refuse anything else for every role.
    if not is_tool_name(tool_name):
        return False
    permission = required_permission(tool_name)
    if permission is None:
        return True
    return rbac.has_permission(role, permission)


def role_for_context(context) -> str | None:
    """Role of the account that owns this conversation.

    Resolved from the owner rather than from the request session: an agent runs
    on a background thread long after the request that started it has finished,
    and the permission check must give the same answer either way.
    """
    owner_id = getattr(context, "owner_id", None)
    if not owner_id:
        return None
    try:
        from helpers import users

        record = users.get_user_by_id(owner_id)
        return record.get("role") if record else None
    except Exception:
        return None


def refusal_message(tool_name: str, role: str | None) -> str:
    if not is_tool_name(tool_name):
        return (
            f"'{tool_name}' is not a tool name. A tool name is one word of letters, digits, '_' and '-', "
            "exactly as the tool list gives it, never a path. Use a tool from the list."
        )
    permission = required_permission(tool_name) or "unknown"
    role_title = rbac.ROLES.get(rbac.normalize_role(role), {}).get("title", "your role")
    return (
        f"The tool '{tool_name}' is not available to you. "
        f"It requires the '{permission}' permission, which {role_title} does not grant. "
        "Do not retry this tool or look for another way to run it. "
        "Tell the user plainly that this action needs a different role, and "
        "continue with what you can do without it."
    )
