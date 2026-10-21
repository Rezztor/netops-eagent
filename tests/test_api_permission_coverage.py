"""Every endpoint is accounted for: gated, or on a list someone signed off.

`helpers/api_policy.py` is built on a claim -- "a new sensitive endpoint that
someone forgets to gate is visible by its absence here" -- and ADR 0004 rests on
it. The claim was structurally true and enforced by nothing, so the absences
accumulated where nobody looked. Ten file-browser endpoints and the handler that
reveals provider API keys in plaintext were all reachable by an account whose
entire permission set is `chat.use`.

This is that claim made mechanical, in the same move the repository already made
for the upstream brand: the rule stops being a habit and starts being a test.

It deliberately does not demand that everything be gated. Most endpoints here
are chat, polling and a user's own notifications, and requiring a permission for
those would be noise that teaches people to add entries without thinking. What it
demands is that every endpoint has been *classified* -- and that adding a new one
fails until somebody decides which list it belongs on.

Updating the baselines below is expected and fine. Doing it without reading the
handler is the failure this guards against.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import api_schema  # noqa: E402

# Endpoints that answer without a session. Each one has a reason, and the reason
# is not "it seemed harmless":
#   api_*            -- the external message API; no session, but requires_api_key
#   bootstrap/health -- the values index.html needs, and liveness
#   logout           -- must work when the session is already gone
#   password_reset_* -- the caller is by definition not signed in
#   cache_reset      -- requires_loopback
#   scheduler_tick   -- requires_loopback
#   ready            -- readiness; authenticating needs the database, so an
#                       authenticated readiness check cannot answer during the
#                       outage it exists to report. Its body is bounded to
#                       ok/ms per check for that reason.
UNAUTHENTICATED = {
    "ready",
    "api_files_get",
    "api_log_get",
    "api_message",
    "api_reset_chat",
    "api_terminate_chat",
    "bootstrap",
    "cache_reset",
    "health",
    "logout",
    "password_reset_confirm",
    "password_reset_request",
    "scheduler_tick",
}

# Authenticated, and any signed-in account may call them: chat, that account's
# own conversations and notifications, and reads that expose nothing about
# anyone else. Captured as a baseline so a *new* endpoint cannot join them
# silently -- the point is the decision, not the list.
#
# An endpoint here that reaches another user's data is a bug in the handler, not
# a missing entry: tests/test_user_isolation.py is where that is asserted.
OPEN_TO_ANY_SIGNED_IN_USER = {
    "agent_profile_set",
    "agents",
    "banners",
    "change_password",
    "chat_create",
    "chat_export",
    "chat_files_path_get",
    "chat_load",
    "chat_remove",
    "chat_reset",
    "chats_search",
    "csrf_token",
    "ctx_window_get",
    "history_get",
    "image_get",
    "message",
    "message_async",
    "message_queue_add",
    "message_queue_remove",
    "message_queue_send",
    "notification_create",
    "notifications_clear",
    "notifications_history",
    "notifications_mark_read",
    "nudge",
    "pause",
    "plugins/_chat_branching/branch_chat",
    "plugins/_chat_compaction/compact_chat",
    "plugins/_chat_naming/chat_name",
    "plugins/_commands/commands",
    "plugins/_ea_ansible/changes_list",
    "plugins/_editor/editor_session",
    "plugins/_kokoro_tts/status",
    "plugins/_kokoro_tts/synthesize",
    "plugins/_memory/knowledge_path_get",
    "plugins/_memory/memory_dashboard",
    "plugins/_model_config/model_config_get",
    "plugins/_model_config/model_override",
    "plugins/_model_config/model_search",
    "plugins/_pin_to_top/get_pins",
    "plugins/_pin_to_top/toggle_pin",
    "plugins/_skills/skills_catalog",
    "plugins/_time_travel/history_diff",
    "plugins/_time_travel/history_list",
    "plugins/_time_travel/history_preview",
    "plugins/_time_travel/history_revert",
    "plugins/_time_travel/history_snapshot",
    "plugins/_time_travel/history_travel",
    "plugins/_time_travel/history_workspaces",
    "plugins/_whisper_stt/status",
    "plugins/_whisper_stt/transcribe",
    "plugins_list",
    "poll",
    "projects",
    "stop",
    "upload",
    "whoami",
}


def _specs():
    return api_schema.collect_endpoints()


def test_no_endpoint_answers_without_a_session_unless_it_is_on_the_list():
    """Authentication fails closed (ADR 0004), so an unauthenticated endpoint is
    a deliberate exception and the set of them is small enough to read."""
    actual = {s["endpoint"] for s in _specs() if not s["requires_auth"]}

    unexpected = actual - UNAUTHENTICATED
    assert not unexpected, (
        "these endpoints answer without a session and are not on the reviewed "
        f"list: {sorted(unexpected)}. If that is intended, add each one to "
        "UNAUTHENTICATED with the reason beside it."
    )


def test_every_unauthenticated_endpoint_is_bounded_by_something_else():
    """No session means the bound has to come from somewhere: an API key, a
    loopback check, or the endpoint genuinely being public."""
    public = {"bootstrap", "health", "logout", "password_reset_confirm",
              "password_reset_request", "ready"}

    for spec in _specs():
        if spec["requires_auth"] or spec["endpoint"] in public:
            continue
        assert spec["requires_api_key"] or spec["requires_loopback"], (
            f"{spec['endpoint']} takes no session, no API key and no loopback "
            "check. Gate it, or add it to `public` here with a reason."
        )


def test_every_authenticated_endpoint_is_gated_or_on_the_reviewed_list():
    """The one that would have caught the file browser and the API-key reveal."""
    ungated = {
        s["endpoint"]
        for s in _specs()
        if s["requires_auth"] and not s["required_permission"]
    }

    unreviewed = ungated - OPEN_TO_ANY_SIGNED_IN_USER
    assert not unreviewed, (
        f"new endpoints reachable by every signed-in account: {sorted(unreviewed)}.\n"
        "Read the handler, then either add it to helpers/api_policy.py with the "
        "permission it needs, or to OPEN_TO_ANY_SIGNED_IN_USER here if any "
        "signed-in user may genuinely call it."
    )


def test_the_reviewed_list_does_not_outlive_its_endpoints():
    """A stale allowlist entry is how a name gets reused later for something
    sensitive and inherits a decision nobody made about it."""
    endpoints = {s["endpoint"] for s in _specs()}
    stale = (OPEN_TO_ANY_SIGNED_IN_USER | UNAUTHENTICATED) - endpoints
    assert not stale, f"listed here but no longer an endpoint: {sorted(stale)}"


def test_the_credential_surfaces_are_gated():
    """Named rather than left to the baseline, because these are the two that
    were found open: reading a provider key back, and writing the instance-wide
    model configuration that carries api_base."""
    by_endpoint = {s["endpoint"]: s for s in _specs()}

    for endpoint in (
        "settings_get",
        "settings_set",
        "plugins/_model_config/api_keys",
        "plugins/_model_config/model_config_set",
        "plugins/_model_config/model_presets",
    ):
        spec = by_endpoint.get(endpoint)
        assert spec, f"{endpoint} is gone; if that is deliberate, drop it here too"
        assert spec["required_permission"] == "settings.manage", (
            f"{endpoint} must require settings.manage; it requires "
            f"{spec['required_permission']!r}"
        )


def test_the_inventory_asset_record_routes_are_gated():
    by_endpoint = {s["endpoint"]: s["required_permission"] for s in _specs()}
    for name, permission in (
        ("inventory_lifecycle", "inventory.view"), ("inventory_quality", "inventory.view"),
        ("inventory_racks", "inventory.view"), ("inventory_template", "inventory.view"),
        ("inventory_summary", "inventory.view"),
        ("inventory_schedule", "inventory.view"),
        ("inventory_propose_many", "inventory.manage"),
        ("inventory_ports", "inventory.view"), ("inventory_vlan", "inventory.view"),
        ("inventory_refresh_ports", "inventory.manage"), ("inventory_port_uplink", "inventory.manage"),
        ("inventory_places", "inventory.view"), ("inventory_place_save", "inventory.manage"),
        ("inventory_place_merge", "inventory.places"),
    ):
        assert by_endpoint.get(f"plugins/_ea_inventory/{name}") == permission, name


def test_the_observed_data_routes_are_gated_in_the_policy_table():
    """Ports and config backup (ADR 0045, 0047, 0049). Asserted on the table, so it holds whichever worker's handler lands first;
    the backup text routes need `manage` because viewing one is a person's act, never the agent's."""
    from helpers import api_policy

    for name, permission in (
        ("inventory_ports", "inventory.view"), ("inventory_vlan", "inventory.view"),
        ("inventory_backups", "inventory.view"), ("inventory_backup_report", "inventory.view"),
        ("inventory_refresh_ports", "inventory.manage"), ("inventory_port_uplink", "inventory.manage"),
        ("inventory_backup_run", "inventory.manage"), ("inventory_backup_view", "inventory.manage"),
        ("inventory_backup_diff", "inventory.manage"),
    ):
        assert api_policy.required_permission_for(f"plugins/_ea_inventory/{name}") == permission, name
