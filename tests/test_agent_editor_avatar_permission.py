"""The avatar upload is the editor's, and needs the editor's grant (audit 6, L2)."""

from helpers import api_schema, rbac
from plugins._agent_editor.api.agent_editor_avatar import AgentEditorAvatar


def test_the_avatar_endpoint_needs_plugins_manage():
    assert AgentEditorAvatar.required_permission() == rbac.PERM_PLUGINS_MANAGE
    spec = {s["endpoint"]: s for s in api_schema.collect_endpoints()}["plugins/_agent_editor/agent_editor_avatar"]
    assert spec["required_permission"] == rbac.PERM_PLUGINS_MANAGE
