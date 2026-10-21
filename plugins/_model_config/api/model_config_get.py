from helpers.api import ApiHandler, Request, Response
from helpers.context_utils import assert_context_access
from helpers import login, rbac
from helpers.providers import get_raw_providers
from plugins._model_config.helpers import model_config
from agent import AgentContext
import models


#: Where a model is reached and with what: the address, which may carry a
#: password, and the extra parameters, where a gateway's key or header goes.
_CONNECTION_FIELDS = ("api_base", "kwargs", "api_key")


def _without_connections(entry):
    """A configuration or a preset with its models' connections left out.

    Every account needs the presets to pick one for a chat, by name. Where a
    preset sends the conversation and what it sends along is the
    administrator's (`model_config_set`, `model_presets`, `api_keys`).
    """
    if not isinstance(entry, dict):
        return entry
    kept = {key: value for key, value in entry.items() if key not in _CONNECTION_FIELDS}
    for key, value in kept.items():
        if isinstance(value, dict):
            kept[key] = {k: v for k, v in value.items() if k not in _CONNECTION_FIELDS}
    return kept


class ModelConfigGet(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        project_name = input.get("project_name", "")
        agent_profile = input.get("agent_profile", "")
        context_id = str(input.get("context_id") or "").strip()
        # The conversation id comes from request input. `assert_context_access`
        # is the codebase's chokepoint for that: it refuses a conversation owned
        # by somebody else in the same words it uses for one that does not exist.
        assert_context_access(context_id)
        context = AgentContext.get(context_id) if context_id else None
        agent = context.root_agent if context else None

        if agent:
            config = model_config.get_effective_config(agent)
            configured_preset = model_config.get_configured_preset_name(agent=agent)
            selected_preset = model_config.get_effective_preset_name(agent)
        else:
            config = model_config.get_config(
                project_name=project_name or None,
                agent_profile=agent_profile or None,
            )
            configured_preset = model_config.get_configured_preset_name(
                project_name=project_name or None,
                agent_profile=agent_profile or None,
            )
            selected_preset = str(
                config.get(model_config.MODEL_PRESET_CONFIG_KEY)
                or model_config.DEFAULT_PRESET_NAME
            )

        # Add provider lists for UI dropdowns
        chat_providers = model_config.get_chat_providers()
        embedding_providers = model_config.get_embedding_providers()
        chat_provider_details = get_raw_providers("chat")
        embedding_provider_details = get_raw_providers("embedding")

        # Mask API keys - show status only
        api_key_status = {}
        all_providers = chat_providers + embedding_providers
        seen = set()
        for p in all_providers:
            pid = p.get("value", "")
            if pid and pid not in seen:
                seen.add(pid)
                key = models.get_api_key(pid)
                api_key_status[pid] = bool(key and key.strip() and key != "None")

        chat_model = config.get("chat_model", {}) if isinstance(config, dict) else {}
        chat_provider = str(chat_model.get("provider") or "").strip()
        chat_name = str(chat_model.get("name") or "").strip()

        presets = model_config.get_presets()
        if not login.has_permission(rbac.PERM_SETTINGS_MANAGE):
            config = _without_connections(config)
            presets = [_without_connections(preset) for preset in presets]

        return {
            "config": config,
            "chat_providers": chat_providers,
            "embedding_providers": embedding_providers,
            "chat_provider_details": chat_provider_details,
            "embedding_provider_details": embedding_provider_details,
            "api_key_status": api_key_status,
            "model_configured": model_config.is_chat_model_configured(config),
            "model_configured_label": " / ".join(
                part for part in (chat_provider, chat_name) if part
            ),
            "presets": presets,
            "configured_preset": configured_preset,
            "selected_preset": selected_preset,
        }
