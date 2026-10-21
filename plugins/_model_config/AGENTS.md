# Model Configuration Plugin DOX

## Purpose

- Own global LLM preset definitions, scoped preset selection, API-key checks, chat overrides, migration, and model settings UI.

## Ownership

- `helpers/model_config.py` owns config resolution, presets, overrides, and runtime model object construction.
- `helpers/ctx_length.py` owns catalogue context-window extraction, LiteLLM overlay, and auto-fill of `ctx_length` on save.
- `api/` owns model config, override, preset, search, and API-key endpoints.
- The React Models screen (`frontend/src/routes/ModelsScreen.tsx`) owns the live Change-a-model editor. Plugin `webui/` is not the live surface.
- `extensions/python/startup_migration/` owns conversion from legacy full configs and project presets, followed by first-launch preset initialization.
- `default_config.yaml`, `mode_presets_fallback.yaml`, `provider_metadata.yaml`, `hooks.py`, and `plugin.yaml` own plugin defaults, offline presets, metadata, hooks, and manifest.

## Local Contracts

- `Default` is the first global preset and cannot be deleted or renamed. It owns the complete main, utility, and embedding baseline.
- Preset definitions are global. Global, project, agent-profile, and project/profile plugin configs persist only `model_preset`; chats may persist a preset reference as their explicit override.
- Preserve scoped plugin resolution order and fall back invalid or missing scope/chat references to `Default`.
- Project Settings `llm` payloads are owned here through the generic `helpers.projects` project extension-data hooks; keep project helper code agnostic to `_model_config` paths, presets, and inheritance rules.
- Keep provider metadata and API-key checks safe around secrets.
- Check API-key readiness only for the effective model configuration; unused global presets must not produce Welcome-screen warnings.
- Coordinate OAuth-backed providers with `_oauth` instead of hardcoding provider-specific auth here.
- `model_config_get` exposes `model_configured` as a derived chat-model readiness flag from provider, model name, and API-key availability.
- `model_config_get` is open to every signed-in account, which needs the presets to pick one by name. Without `settings.manage` the answer leaves out each model's connection (`api_base`, `kwargs`, `api_key`) in the configuration and in every preset: an address may carry a password and the extra parameters are where a gateway's key goes. `tests/test_model_config_get_roles.py` covers this.
- Non-default presets may inherit omitted slots or durable tuning from `Default`, but must replace or clear per-slot `kwargs` so provider-specific extra params never leak across model providers.
- Changing a model provider in the settings UI must clear `api_base` and `kwargs` because both may be provider-specific.
- Repair provider-specific model-config aliases at the model-config read/build boundary; keep provider-specific repairs out of provider-agnostic core wrappers such as `models.py`.
- `modelConfig.createPresetEditor()` owns local preset drafts, row actions, and stable UI-only row keys so deletion or renaming cannot rebind nested model fields.
- The preset editor maps each model provider's API-key field to the shared API-key store; saving the editor persists dirty keys separately and never writes secrets into preset YAML.
- The compact chat selector label combines the effective preset with only the leaf name of its main model; utility and provider text stay out of the closed selector.
- The adjacent agent-profile selector reads the always-enabled Agent Editor list
  endpoint directly so the active profile shows its effective title and avatar,
  and omits profiles disabled in the chat's current scope plus the exact
  `default` utility profile. A chat already using `default` may still show that
  current status without adding a selectable or editable row.
- Reload the agent-profile selector catalog when a chat changes project or
  active profile so project-only profiles never linger in the visible choices.
- Concurrent agent-profile catalog loads for the same chat share one request;
  across chats, only the newest request may replace selector state or finish
  its loading lifecycle.
- Preset editor reset actions must remove the user override through the preset API and refresh the open draft from bundled defaults.
- Preset rename, delete, and reset actions must repair scoped config and durable/live chat references; removed definitions fall back to `Default`.
- Migration must preserve existing definitions and distinct scoped model choices, back up replaced user files once, strip inline secrets, and remain idempotent.
- On every startup after migration, short-circuit when `usr/plugins/_model_config/presets.yaml` exists. Only a missing collection may fetch `rezztor/ea-presets`; parse remote and plugin-local fallback YAML through the same validator, strip secrets before persistence, and persist `mode_presets_fallback.yaml` when download or validation fails.
- `model_search` returns `{name, ctx_length?}` objects. Omit `ctx_length` when unknown.
- Ollama Cloud's `/v1/models` carries no context window, so `model_search` asks `/api/show` on the same host for each model that lacks one. Keep this to `ollama_cloud`: a local Ollama serves the window it was started with, which `/api/ps` reports, not the one the model could take.
- Chat and utility slots auto-detect `ctx_length` on save unless `ctx_length_manual` is true. Embedding slots omit both fields. Existing presets without the flag load as auto.
- The Change-a-model editor lists the provider catalogue in a select, keeps a Custom id escape, and toggles manual context length. Fetch the full catalogue on provider change; do not filter the list by the current model id.

- `model_search` and the free-form `model_override` `set` action need `settings.manage`: both take an `api_base` from the request and would send the instance's provider key to it. `set_preset`, `get` and `clear` stay open to the conversation's owner.

## Work Guidance

- Keep backend model config shape and frontend settings fields synchronized.

## Verification

- Run model-config and onboarding-related tests when model provider, preset, or API-key behavior changes.

## Child DOX Index

No child DOX files.
