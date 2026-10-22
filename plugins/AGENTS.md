# Plugins DOX

## Purpose

- Own bundled system plugins shipped with Rezztor EAgent.
- Provide the tracked plugin architecture contract for both bundled plugins and custom plugins developed under ignored `usr/plugins/`.
- Keep plugin behavior discoverable, reversible, and compatible across bundled and custom roots without creating DOX files inside `usr/`.

## Ownership

- Each direct `_plugin_name/` directory owns its `plugin.yaml`, optional `default_config.yaml`, `hooks.py`, API handlers, helpers, tools, prompts, skills, extensions, WebUI assets, and README.
- Plugins may also own `execute.py`, `conf/model_providers.yaml`, plugin-distributed `agents/<profile>/agent.yaml`, static assets, and plugin-local docs.
- `README.md` owns the high-level core plugin architecture summary and community Plugin Index guidance.
- Custom or experimental user plugins belong under `usr/plugins/`, not here, but this file remains their tracked DOX contract.
- Do not create `AGENTS.md` files under `usr/plugins/` unless the user explicitly asks for ignored user-state documentation.

## Local Contracts

- Every plugin directory must include a valid `plugin.yaml`.
- Bundled plugin directory names and manifest `name` values must start with `_` to avoid collisions with community plugins.
- Runtime manifest fields include `name`, `title`, `description`, `version`, `settings_sections`, `per_project_config`, `per_agent_config`, and `always_enabled`.
- Core plugins may use `plugins.<plugin_name>...` imports when they are shipped from this tree.
- User plugins under `usr/plugins/` must use `usr.plugins.<plugin_name>...` imports and avoid `sys.path` hacks or persistent symlink-based imports.
- Plugin extension layouts must use `extensions/python/<point>/`, `extensions/python/_functions/<module>/<qualname>/<start|end>/`, and `extensions/webui/<point>/`.
- The `_functions` extension layout preserves every module and nested qualname segment; do not use retired flattened extension folder names.
- Plugin settings defaults belong in `default_config.yaml`; runtime user settings belong under `usr/`.
- Plugin settings resolution order is project/profile, project, user/profile, user plugin config, then bundled `default_config.yaml`.
- `webui/config.html` settings UIs must bind plugin values to `config.*` and modal state/actions to `context.*` through `$store.pluginSettingsPrototype`.
- Plugin model provider overrides belong in plugin `conf/model_providers.yaml` and merge after base `conf/model_providers.yaml`.
- Global and scoped activation are independent and use `.toggle-1` and `.toggle-0`; `always_enabled: true` forces ON and disables UI toggles.
- `hooks.py` runs in the framework runtime. Explicitly target another runtime if a plugin must prepare the agent execution environment.
- `execute.py` is manual user-triggered setup, maintenance, repair, migration, or refresh work; automatic framework behavior belongs in hooks or lifecycle extensions.
- Plugin routes are `GET /plugins/<name>/<path>`, `POST /api/plugins/<name>/<handler>`, and `POST /api/plugins` for management actions.
- Frontend plugin HTML extensions live under `extensions/webui/<point>/`, include a root Alpine scope, and use `x-move-*` directives when targeting static breakpoints.
- Frontend plugin JS extensions live under `extensions/webui/<point>/` and export a default function.
- Plugin UI must use the EAgent notification system for errors, warnings, success, and info instead of inline success/error boxes.
- Bundled plugin WebUI must author Material Symbols as empty `<x-icon name="lowercase_snake_case"></x-icon>` elements and use `:name` for Alpine-driven icon names. Legacy `.material-symbols-outlined` and `.material-icons-outlined` spans remain runtime-compatible for community plugins, but bundled plugins must not introduce new ligature-text spans.
- Banners and discovery cards are provided through Python `banners` extensions by appending dictionaries with unique `id`, `type`, `priority`, and display fields to the `banners` list.
- Alert banner types are `info`, `warning`, and `error`; discovery card types are `hero` and `feature`.
- Banner/card fields may include `title`, `html`, `description`, `thumbnail`, `icon`, `cta_text`, `cta_action`, and `dismissible` depending on type.
- Community discovery cards should use `type: "feature"`; reserve `hero` cards for core system features.
- Supported discovery CTA actions are `open-plugin-config:<plugin_folder_name>`, `open-plugin-hub`, and `open-url:<url>`.
- Plugin deletion or disablement should not leave unmanaged services, symlinks, or files outside plugin-owned paths unless explicitly documented with cleanup.

## Work Guidance

- Prefer plugin-local helpers for behavior used only by one plugin.
- Use shared `helpers/` only for reusable framework behavior.
- Use the notification system for plugin UI feedback.
- Keep plugin README and docs current when user-visible plugin behavior changes.
- Check configuration before injecting setup or discovery banners so configured plugins do not keep advertising setup.
- Use highly unique banner IDs prefixed by plugin name.
- When preparing community plugins, keep plugin contents at the standalone repository root with `plugin.yaml`, `README.md`, and a root `LICENSE`.
- Plugin Index submissions use a separate `index.yaml` under `ea-plugins/plugins/<name>/`; do not confuse it with runtime `plugin.yaml`.

## Verification

- Run plugin-specific tests after changing a bundled plugin.
- Run framework tests for touched extension points, API handlers, tools, settings, or WebUI surfaces.
- For plugins with external services, perform a targeted smoke check when practical.
- For banner/discovery changes, verify the Welcome Screen renders alert banners, feature cards, dismiss behavior, priority ordering, and CTA behavior.

## Child DOX Index

Direct child DOX files:

| Child | Scope |
| --- | --- |
| [_agent_editor/AGENTS.md](_agent_editor/AGENTS.md) | Deterministic sparse agent-profile editor API, helpers, and WebUI. |
| [_chat_branching/AGENTS.md](_chat_branching/AGENTS.md) | Chat branching from an existing message. |
| [_chat_compaction/AGENTS.md](_chat_compaction/AGENTS.md) | Full-chat compaction into a summary message. |
| [_chat_naming/AGENTS.md](_chat_naming/AGENTS.md) | Built-in manual and Utility Model-assisted chat naming. |
| [_commands/AGENTS.md](_commands/AGENTS.md) | Built-in slash command manager, command file discovery, and chat composer slash picker. |
| [_code_execution/AGENTS.md](_code_execution/AGENTS.md) | Terminal, Python, and Node.js execution tools and shell runtimes. |
| [_ea_ansible/AGENTS.md](_ea_ansible/AGENTS.md) | Network devices and firewalls through a dedicated Ansible executor. Reads are free; changes need an engineer's approval of the exact commands. |
| [_ea_attachments/AGENTS.md](_ea_attachments/AGENTS.md) | Delivering a file from the agent to the user as a download. |
| [_ea_azure/AGENTS.md](_ea_azure/AGENTS.md) | Reading an Azure tenant into a dated inventory snapshot with a change log. Read-only. |
| [_ea_azure_change/AGENTS.md](_ea_azure_change/AGENTS.md) | Adding to or changing Azure from the chat through the cloud executor, behind the engineer's approval, a CR/SR number and a change document. Never deletes or stops anything. |
| [_ea_inventory/AGENTS.md](_ea_inventory/AGENTS.md) | The instance's device inventory: proposals an engineer approves, per-field provenance, sync to the Ansible executor. |
| [_ea_interaction/AGENTS.md](_ea_interaction/AGENTS.md) | Questions the user answers by clicking, carried on the log item's kvps. |
| [_ea_mail/AGENTS.md](_ea_mail/AGENTS.md) | Emailing people who hold accounts on this instance. |
| [_ea_solarwinds/AGENTS.md](_ea_solarwinds/AGENTS.md) | Reading live SolarWinds data into the agent's answers. Read-only. |
| [_ea_siem_forward/AGENTS.md](_ea_siem_forward/AGENTS.md) | Forwarding the audit trail to a Syslog/SIEM collector as CEF over UDP. Observes the trail, never alters it. |
| [_ea_auto_skills/AGENTS.md](_ea_auto_skills/AGENTS.md) | Induction of reusable skills from completed tasks, private per user until promoted. |
| [_document_query/AGENTS.md](_document_query/AGENTS.md) | Document parsing, indexing, and Q&A tools. |
| [_editor/AGENTS.md](_editor/AGENTS.md) | Native Markdown editor surface and sessions. |
| [_email_integration/AGENTS.md](_email_integration/AGENTS.md) | IMAP/Exchange polling and SMTP reply integration. |
| [_error_retry/AGENTS.md](_error_retry/AGENTS.md) | Critical exception retry lifecycle hooks. |
| [_infection_check/AGENTS.md](_infection_check/AGENTS.md) | Prompt-injection safety analysis before tool execution. |
| [_kokoro_tts/AGENTS.md](_kokoro_tts/AGENTS.md) | Kokoro text-to-speech integration. |
| [_memory/AGENTS.md](_memory/AGENTS.md) | Optional persistent recall plugin, knowledge import, tools, and dashboard; do not assume it is enabled outside this plugin. |
| [_model_config/AGENTS.md](_model_config/AGENTS.md) | Model selection, presets, API-key checks, and scoped overrides. |
| [_pin_to_top/AGENTS.md](_pin_to_top/AGENTS.md) | Built-in chat and task sidebar pinning. |
| [_promptinclude/AGENTS.md](_promptinclude/AGENTS.md) | Promptinclude scanning and prompt injection. |
| [_skills/AGENTS.md](_skills/AGENTS.md) | Active and hidden skill configuration and prompt injection. |
| [_text_editor/AGENTS.md](_text_editor/AGENTS.md) | Native text read, write, and patch tool. |
| [_time_travel/AGENTS.md](_time_travel/AGENTS.md) | Workspace history, diff, travel, snapshot, and revert flows. |
| [_tool_access/AGENTS.md](_tool_access/AGENTS.md) | Always-on project/profile tool-policy execution gate. |
| [_whisper_stt/AGENTS.md](_whisper_stt/AGENTS.md) | Whisper speech-to-text integration. |
