"""Role-based access control for Rezztor EAgent.

Permissions are declared here, roles map to sets of permissions, and every
enforcement point in the app asks this module rather than testing role names
directly. Adding a role is a data change in ROLES, not a code change.
"""

from __future__ import annotations

from typing import Iterable


# --- Permissions ------------------------------------------------------------
# Coarse enough to reason about, fine enough to gate the things that matter.

PERM_USERS_MANAGE = "users.manage"  # create/edit/disable/delete accounts
PERM_SETTINGS_MANAGE = "settings.manage"  # model providers, API keys, framework config
PERM_PLUGINS_MANAGE = "plugins.manage"  # enable/disable/install plugins
PERM_SCHEDULER_MANAGE = "scheduler.manage"  # create/modify scheduled tasks
PERM_AUDIT_VIEW_ALL = "audit.view_all"  # see other users' activity
PERM_CHAT_USE = "chat.use"  # hold a conversation at all
PERM_TOOL_CODE_EXECUTION = "tools.code_execution"  # run shell/python/node
PERM_TOOL_SUBORDINATES = "tools.subordinates"  # spawn subordinate agents
PERM_TOOL_NETWORK = "tools.network"  # use the network tools at all
PERM_NETWORK_APPROVE = "network.approve"  # approve the commands a change would push
PERM_NETWORK_VIEW = "network.view"  # read the job history, the inventory and the catalogue
PERM_INVENTORY_VIEW = "inventory.view"  # read the device inventory and its proposals
PERM_INVENTORY_MANAGE = "inventory.manage"  # propose inventory changes, add floors, rooms and racks
PERM_INVENTORY_APPROVE = "inventory.approve"  # decide an inventory proposal, sync the executor
PERM_INVENTORY_PLACES = "inventory.places"  # add and rename sites, locations and buildings; delete or adopt places

ALL_PERMISSIONS: tuple[str, ...] = (
    PERM_USERS_MANAGE,
    PERM_SETTINGS_MANAGE,
    PERM_PLUGINS_MANAGE,
    PERM_SCHEDULER_MANAGE,
    PERM_AUDIT_VIEW_ALL,
    PERM_CHAT_USE,
    PERM_TOOL_CODE_EXECUTION,
    PERM_TOOL_SUBORDINATES,
    PERM_TOOL_NETWORK,
    PERM_NETWORK_APPROVE,
    PERM_NETWORK_VIEW,
    PERM_INVENTORY_VIEW,
    PERM_INVENTORY_MANAGE,
    PERM_INVENTORY_APPROVE,
    PERM_INVENTORY_PLACES,
)


# --- Roles ------------------------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_POWER = "power"
ROLE_USER = "user"

ROLES: dict[str, dict] = {
    ROLE_ADMIN: {
        "title": "Administrator",
        "description": "Full control: user management, settings, plugins, and every tool.",
        "permissions": set(ALL_PERMISSIONS),
    },
    ROLE_POWER: {
        "title": "Power User",
        "description": "Chat plus code execution, subordinate agents, and the network tools. Can read what ran, approve the network changes they requested, and keep the device inventory (the network engineer).",
        "permissions": {
            PERM_CHAT_USE,
            PERM_TOOL_CODE_EXECUTION,
            PERM_TOOL_SUBORDINATES,
            PERM_TOOL_NETWORK,
            PERM_NETWORK_APPROVE,
            PERM_NETWORK_VIEW,
            PERM_INVENTORY_VIEW,
            PERM_INVENTORY_MANAGE,
            PERM_INVENTORY_APPROVE,
            PERM_SCHEDULER_MANAGE,
        },
    },
    ROLE_USER: {
        "title": "Standard User",
        "description": "Chat, memory, and knowledge only. No code execution or network device access.",
        "permissions": {
            PERM_CHAT_USE,
        },
    },
}

DEFAULT_ROLE = ROLE_USER


def is_valid_role(role: str | None) -> bool:
    return bool(role) and role in ROLES


def normalize_role(role: str | None) -> str:
    """Coerce an unknown or missing role to the least-privileged role.

    Unknown roles must never widen access, so anything unrecognised collapses
    to DEFAULT_ROLE rather than raising.
    """
    return role if is_valid_role(role) else DEFAULT_ROLE


def get_permissions(role: str | None) -> set[str]:
    return set(ROLES[normalize_role(role)]["permissions"])


def has_permission(role: str | None, permission: str) -> bool:
    return permission in get_permissions(role)


def has_all_permissions(role: str | None, permissions: Iterable[str]) -> bool:
    granted = get_permissions(role)
    return all(p in granted for p in permissions)


def is_admin(role: str | None) -> bool:
    return normalize_role(role) == ROLE_ADMIN


def list_roles() -> list[dict]:
    """Role catalogue for the admin UI."""
    return [
        {
            "id": role_id,
            "title": spec["title"],
            "description": spec["description"],
            "permissions": sorted(spec["permissions"]),
        }
        for role_id, spec in ROLES.items()
    ]
