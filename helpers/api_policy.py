"""Central RBAC policy for API endpoints.

Kept as one table rather than a `required_permission()` override in each of the
~80 handler files: a single list can be reviewed in one sitting, and a new
sensitive endpoint that someone forgets to gate is visible by its absence here.

A handler's own `required_permission()` still wins if it declares one, so
plugins can gate their endpoints without touching this file.
"""

from __future__ import annotations

from helpers import rbac

# endpoint path (the part after /api/) -> required permission
API_PERMISSIONS: dict[str, str] = {}


def _grant(permission: str, *endpoints: str) -> None:
    for endpoint in endpoints:
        API_PERMISSIONS[endpoint] = permission


# --- Administration: framework configuration, credentials, lifecycle ---------
# settings_get returns the whole configuration document including provider
# credentials, so it is administrator-only for reading as well as writing.
_grant(
    rbac.PERM_SETTINGS_MANAGE,
    "settings_get",
    "settings_set",
    "settings_workdir_file_structure",
    "mcp_servers_apply",
    "mcp_servers_status",
    "mcp_server_get_detail",
    "mcp_server_get_log",
    "mcp_server_scan",
    "backup_create",
    "backup_get_defaults",
    "backup_inspect",
    "backup_preview_grouped",
    "backup_restore",
    "backup_restore_preview",
    "backup_test",
    "cache_reset",
    "restart",
    # Licence administration. Both handlers also declare this themselves;
    # listed here so the licence surface is visible in the one table meant to
    # be reviewable in a sitting. license_activate takes the activation key,
    # claims and releases an installation slot of the customer's licence and
    # loads an offline licence file -- not a per-user action. Neither returns
    # the installation token or the activation key.
    "license_status",
    "license_activate",
    # The appliance's update service (ADR 0080). Both handlers also declare this
    # themselves. update_apply stores an uploaded update file and asks the host
    # tool to check, install, restart or discard; the host verifies the file's
    # signature itself, so this is the right to ask, not the power to install.
    "update_status",
    "update_apply",
    # A support bundle the host made: logs and settings of the whole machine, cleaned of secrets. For the
    # administrator to read and send to Rezztor; every download is audited.
    "support_bundle_download",
    # Model configuration is instance-wide, and these write it. The saved
    # preset carries api_base, so an account that could change it could point
    # every user's chat, utility and embedding traffic at a host of its own --
    # and model_config_set writes API_KEY_* into usr/.env on the way past.
    # model_override is deliberately NOT here: it sets one chat's model and
    # goes through assert_context_access, so it is the caller's own to change.
    "plugins/_model_config/model_config_set",
    "plugins/_model_config/model_presets",
    # Reading a provider key back in plaintext, and writing one. The handler
    # declares this itself as well; listed here because this table is where a
    # reader checks what an account can reach, and a credential-revealing
    # endpoint has to be visible in it.
    "plugins/_model_config/api_keys",
    # Takes a mail server, a port and credentials from the request and makes
    # this container connect to them, reporting what happened. That is an
    # outbound probe aimed by the caller -- usable against hosts on the
    # deployment's own network that nothing else here can reach -- and the
    # configuration it tests is instance-wide. No frontend calls it.
    "plugins/_email_integration/test_connection",
)

# --- Account administration -------------------------------------------------
_grant(
    rbac.PERM_USERS_MANAGE,
    "users_list",
    "users_create",
    "users_update",
    "users_delete",
)

# --- Plugins and skills: they can add tools and executable code -------------
# Gate by dispatch path, not by which directory the handler happens to live in.
# This table once held only core endpoint names, which left every plugin-owned
# endpoint ungated -- including plugin installation, reachable by the
# least-privileged role. The plugins that exposed those routes have since been
# removed, but the rule stands for any plugin route added later: put it here.
_grant(
    rbac.PERM_PLUGINS_MANAGE,
    "plugins",
    "skills_import",
    "skills_import_preview",
    "skills_scan",
    # "skills" carries action=delete alongside action=list, and the table gates
    # by endpoint rather than by action. Deleting a skill changes what every
    # agent on the instance is able to do, so the endpoint is gated and listing
    # is served by plugins/_skills/skills_catalog, which only reads.
    "skills",
    # usr/knowledge is shared, not per-user, and it is RAG-indexed and quoted
    # back by every agent. An ungated import is therefore not a denial of
    # service but a prompt-injection channel into everyone else's conversations:
    # any signed-in account could have written instructions the agent then
    # treats as reference material.
    "plugins/_memory/import_knowledge",
    # Rebuilds that shared index. Instance-wide and expensive.
    "plugins/_memory/knowledge_reindex",
    # Promoting an induced skill copies it into usr/skills, which is read into
    # every account's system prompt -- the same exposure as knowledge import,
    # and the content was written by a model rather than by a person.
    "plugins/_ea_auto_skills/auto_skills_promotions",
    # Decides whether the agent induces skills at all, and for whom.
    "plugins/_ea_auto_skills/auto_skills_config",
    # Decides which SolarWinds instance the agent reads live infrastructure
    # data from, and holds the connection credentials.
    "plugins/_ea_solarwinds/solarwinds_config",
    # Decides where a copy of this instance's whole audit trail is sent. An
    # account that could point that at a host of its own choosing could
    # exfiltrate every recorded action on the instance.
    "plugins/_ea_siem_forward/siem_forward_config",
    # Sends to that destination on demand.
    "plugins/_ea_siem_forward/siem_forward_test",
    # Decides which Ansible executor the agent sends network jobs to.
    "plugins/_ea_ansible/ansible_config",
    # Editing the inventory and the device credentials. Same permission as
    # configuring the executor, because both decide what the agent can reach.
    "plugins/_ea_ansible/network_inventory",
    # Agent profiles are the system prompt and tool policy every account's
    # agent loads. Its save/duplicate/delete actions rewrite them instance-wide,
    # which is the prompt-injection-into-everyone's-conversations exposure the
    # _memory/import_knowledge entry above is gated for.
    "plugins/_agent_editor/agent_editor",
    # The same profiles through the core endpoint: its save and delete actions
    # rewrite `usr/agents`, which every account's agent loads. It was left open
    # to any signed-in account while the editor above was gated.
    "subagents",
)

# --- User-scoped, and deliberately not administrative ------------------------
# Auto Skills writes skills into the caller's own usr/users/<id>/skills, which
# only their agent reads. Removing one is theirs to do: the feature applies them
# without asking, so requiring an administrator to undo that would be the wrong
# default. Listed here rather than left ungated so that revoking chat access
# revokes this too, and so the table shows the endpoint was considered.
_grant(
    rbac.PERM_CHAT_USE,
    "plugins/_ea_auto_skills/auto_skills_forget",
    # An attachment is a file this account was given in its own conversation.
    # The handler resolves the owner from the session and takes no user id, so
    # an id from someone else's transcript resolves to a path in the caller's
    # own directory and is simply absent.
    "plugins/_ea_attachments/attachment_get",
    # A user's own reminders and schedules, unlike scheduler_task_* below: each
    # handler filters or checks by owner_id itself (helpers.task_scheduler.
    # assert_task_owner), so opening these to every signed-in account cannot
    # reach a task belonging to someone else. Listed here rather than left
    # ungated for the same reason as auto_skills_forget above.
    "scheduler_my_tasks",
    "scheduler_my_task_run",
    "scheduler_my_task_pause",
    "scheduler_my_task_resume",
    "scheduler_my_task_delete",
    # Bulk delete of the caller's own conversations. Every id goes through
    # assert_context_access, so another account's chat is refused per id and the
    # rest of the batch still proceeds.
    "chat_remove_many",
    # The file browser. Each of these resolves through helpers.file_browser,
    # which is rooted at the caller's own working directory -- that root is what
    # keeps one account out of another's files, not this line. They are listed
    # here for the two reasons auto_skills_forget is: revoking chat access
    # revokes them too, and the table is where a reader checks what an account
    # can reach, so an endpoint that touches files must appear in it.
    #
    # They were absent entirely while FileBrowser was rooted at "/", which is
    # how a `chat.use` account could read /etc and usr/.env. The absence was the
    # bug this table was designed to make visible; nothing was looking.
    "get_work_dir_files",
    "download_work_dir_file",
    "download_work_dir_files",
    "upload_work_dir_files",
    "edit_work_dir_file",
    "delete_work_dir_file",
    "delete_work_dir_files",
    "rename_work_dir_file",
    "extract_work_dir_archive",
    "file_info",
)

# --- Audit: these events name other users and what they ran -----------------
_grant(
    rbac.PERM_AUDIT_VIEW_ALL,
    "plugins/_audit/audit_recent",
)

# --- The network console: reading is not administering ----------------------
# Deliberately a lower bar than plugins.manage. An engineer who needs to see
# what was pushed to a switch last Tuesday should not need the permission that
# lets them point the agent at a different executor -- and a screen hidden from
# them is a screen they work around.
_grant(
    rbac.PERM_NETWORK_VIEW,
    "plugins/_ea_ansible/network_jobs",
    # The appliance's networks, read-only: the same bar as the job history it sits beside.
    "plugins/_ea_ansible/network_list",
)

# --- Approving a change: the one decision the agent can never make ----------
# Listed here as well as on the handlers, because this table is where a reader
# checking "what can this account reach" actually looks. The requesting engineer
# (power role) approves their own change and is accountable for it (ADR 0039).
_grant(
    rbac.PERM_NETWORK_APPROVE,
    "plugins/_ea_ansible/change_approve",
    "plugins/_ea_ansible/changes_review",
    # The Approvals page: what the caller may sign, across users for a device change, their own for Azure.
    "plugins/_ea_ansible/approvals_waiting",
)

# --- The device inventory (ADR 0040) ----------------------------------------
# Instance-wide data, so these permissions are the whole access control. Reading
# and proposing are separate from deciding: the agent and any `manage` holder can
# propose, only `approve` changes the inventory. `inventory_place_save` is gated on
# manage here and checks `inventory.places` itself for the upper three levels, a chain and
# every delete, because one route holds one permission; `inventory_place_merge` declares
# `inventory.places` on the handler.
_grant(
    rbac.PERM_INVENTORY_VIEW,
    "plugins/_ea_inventory/inventory_list",
    "plugins/_ea_inventory/inventory_device",
    "plugins/_ea_inventory/inventory_proposals",
    "plugins/_ea_inventory/inventory_batches",
    "plugins/_ea_inventory/inventory_places",
    "plugins/_ea_inventory/inventory_export",
    "plugins/_ea_inventory/inventory_sources",
    "plugins/_ea_inventory/inventory_conflicts",
    "plugins/_ea_inventory/inventory_pins",
    "plugins/_ea_inventory/inventory_stale",
    "plugins/_ea_inventory/inventory_lifecycle",
    "plugins/_ea_inventory/inventory_quality",
    "plugins/_ea_inventory/inventory_racks",
    "plugins/_ea_inventory/inventory_template",
    "plugins/_ea_inventory/inventory_summary",
    "plugins/_ea_inventory/inventory_schedule",
    "plugins/_ea_inventory/inventory_ports",
    "plugins/_ea_inventory/inventory_vlan",
    "plugins/_ea_inventory/inventory_backups",
    "plugins/_ea_inventory/inventory_backup_report",
    # Config that changed, matched against the applied changes: backup metadata and change ids, never commands.
    "plugins/_ea_inventory/inventory_drift",
)
# Observed data (ADR 0045): a person's click on Read from device / Back up now is the approval, so
# these need `manage`, not `approve`. Backup text comes back only redacted, and only to `manage`.
_grant(
    rbac.PERM_INVENTORY_MANAGE,
    "plugins/_ea_inventory/inventory_propose",
    "plugins/_ea_inventory/inventory_propose_many",
    "plugins/_ea_inventory/inventory_place_save",
    "plugins/_ea_inventory/inventory_refresh_facts",
    "plugins/_ea_inventory/inventory_refresh_ports",
    "plugins/_ea_inventory/inventory_port_uplink",
    "plugins/_ea_inventory/inventory_backup_run",
    "plugins/_ea_inventory/inventory_backup_view",
    "plugins/_ea_inventory/inventory_backup_diff",
)
_grant(
    rbac.PERM_INVENTORY_APPROVE,
    "plugins/_ea_inventory/inventory_decide",
    "plugins/_ea_inventory/inventory_decide_batch",
    "plugins/_ea_inventory/inventory_sync",
    "plugins/_ea_inventory/inventory_sync_solarwinds",
    "plugins/_ea_inventory/inventory_conflict_decide",
    "plugins/_ea_inventory/inventory_conflict_decide_many",
    "plugins/_ea_inventory/inventory_unpin",
)
_grant(rbac.PERM_INVENTORY_MANAGE, "plugins/_ea_inventory/inventory_propose_decommission_stale")

# --- Azure inventory: a read-only snapshot of a tenant ----------------------------------------
# Configuring it decides which tenant is read and holds its credential. Reading is the inventory's own
# permission; a pull is a person's click (or the schedule), so it needs `manage` and no approval.
_grant(rbac.PERM_PLUGINS_MANAGE, "plugins/_ea_azure/azure_config", "plugins/_ea_azure/azure_test")
_grant(
    rbac.PERM_INVENTORY_VIEW,
    "plugins/_ea_azure/azure_status",
    "plugins/_ea_azure/azure_tree",
    "plugins/_ea_azure/azure_resources",
    "plugins/_ea_azure/azure_resource",
    "plugins/_ea_azure/azure_history",
    "plugins/_ea_azure/azure_waste",
    "plugins/_ea_azure/azure_cost",
)
_grant(rbac.PERM_INVENTORY_MANAGE, "plugins/_ea_azure/azure_pull")

# --- Azure changes from the chat (ADR 0061 to 0066) ---------------------------------------------------------
# Approving is the requester's click and needs a CR/SR number; the handler checks both. Editing a card and
# downloading one's own document are the engineer's own work (`tools.network`); `azure_document` declares
# `network.approve` on the handler for another user's record, as `changes_review` does. Guard rails, keys and
# the reference pattern are administration. The Changes screen reads the stored records, so it is `inventory.view`.
_grant(
    rbac.PERM_NETWORK_APPROVE,
    "plugins/_ea_azure_change/azure_approve",
    "plugins/_ea_azure/azure_read_approve",
)
_grant(
    rbac.PERM_TOOL_NETWORK,
    "plugins/_ea_azure_change/azure_edit",
    "plugins/_ea_azure_change/azure_document",
    "plugins/_ea_azure/azure_read_edit",
)
_grant(rbac.PERM_PLUGINS_MANAGE, "plugins/_ea_azure_change/azure_change_config")
_grant(rbac.PERM_INVENTORY_VIEW, "plugins/_ea_azure_change/azure_changes")

# --- Scheduled tasks: they run agents unattended ----------------------------
# The outer gate only. A task runs as its owner, so each handler also checks
# that the caller owns the task or is an administrator
# (helpers.task_scheduler.assert_task_access); the list shows a caller their own.
_grant(
    rbac.PERM_SCHEDULER_MANAGE,
    "scheduler_task_create",
    "scheduler_task_update",
    "scheduler_task_delete",
    "scheduler_task_run",
    "scheduler_tasks_list",
)


def required_permission_for(path: str) -> str | None:
    """Permission required for an /api/<path> endpoint, or None if any signed-in
    user may call it."""
    return API_PERMISSIONS.get(path.strip("/"))


# --- What a Restricted installation does not start ---------------------------
# No licence, or a licence past its grace: sign-in, reading, export, settings
# and the licence page work; nothing new starts (helpers/license_entitlements).
# Agent runs and scheduled tasks are refused at their own chokepoints. These
# are the endpoints that start something without going through either: the
# approval that lets a device or cloud change run, and a task run by hand. The
# dispatcher answers them 403 with the sentence that names the licence page.
REFUSED_WHEN_RESTRICTED = frozenset({
    "plugins/_ea_ansible/change_approve",
    "plugins/_ea_azure_change/azure_approve",
    "scheduler_task_run",
    "scheduler_my_task_run",
})
