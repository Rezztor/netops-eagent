# settings.py DOX

## Purpose

- Own the `settings.py` helper module.
- This module defines settings models, defaults, validation, and serialization.
- Keep this file-level DOX profile synchronized with `settings.py` because this directory is intentionally flat.

## Ownership

- `settings.py` owns the runtime implementation.
- `settings.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `Settings` (`TypedDict`)
- `PartialSettings` (`Settings`)
- `FieldOption` (`TypedDict`)
- `SettingsField` (`TypedDict`)
- `SettingsSection` (`TypedDict`)
- `ModelProvider` (`ProvidersFO`)
- `SettingsOutputAdditional` (`TypedDict`)
- `SettingsOutput` (`TypedDict`)
- Top-level functions:
- `get_default_value(name: str, value: T) -> T`: Load setting value from .env with REZZTOR_SET_ prefix, falling back to default.
- `_ensure_option_present(options: list[OptionT] | None, current_value: str | None) -> list[OptionT]`: Ensure the currently selected value exists in a dropdown options list.
- `_is_valid_timezone(value: str) -> bool`
- `_normalize_timezone_setting(value: Any, default: str=...) -> str`
- `_normalize_time_format(value: Any, default: str=...) -> str`
- `_normalize_ui_control_visibility(value: Any) -> dict[str, dict[str, bool]]`
- `_resolve_runtime_timezone(setting_value: str, browser_timezone: str | None=...) -> str`
- `apply_browser_timezone(browser_timezone: str | None) -> None`: what the timezone a request carries may do. With the `timezone` setting on `auto` the instance's clock follows it when the account behind the request holds `settings.manage`, and no other account's (one clock serves the instance and scheduled tasks run by it, so followed from everybody it moved between the zones of whoever asked last); with a zone an administrator fixed it changes nothing. Polls (`helpers/state_snapshot.py`) and the scheduler endpoints call this and never `Localization.set_timezone` themselves (`tests/test_timezone_regressions.py`). The clock is one per instance: on `auto`, with people in two zones, it follows whoever asked last.
- `_timezone_options() -> list[FieldOption]`
- `convert_out(settings: Settings) -> SettingsOutput`
- `_get_api_key_field(settings: Settings, provider: str, title: str) -> SettingsField`
- `convert_in(settings: Settings) -> Settings`
- `get_settings() -> Settings`
- `reload_settings() -> Settings`
- `set_runtime_settings_snapshot(settings: Settings) -> None`
- `set_settings(settings: Settings, apply: bool=..., browser_timezone: str | None=...)`
- `set_settings_delta(delta: dict, apply: bool=...)`
- `merge_settings(original: Settings, delta: dict) -> Settings`
- `normalize_settings(settings: Settings) -> Settings`
- `_adjust_to_version(settings: Settings, default: Settings)`
- `_load_sensitive_settings(settings: Settings)`
- `_read_settings_file() -> Settings | None`
- `_write_settings_file(settings: Settings, conn=None)`
- `_remove_sensitive_settings(settings: Settings)`
- `_write_sensitive_settings(settings: Settings)`
- `get_default_settings() -> Settings`
- `_apply_timezone_setting(previous: Settings | None, browser_timezone: str | None=...) -> None`
- `_apply_settings(previous: Settings | None, browser_timezone: str | None=...)`
- `_env_to_dict(data: str)`
- `_dict_to_env(data_dict)`
- `set_root_password(password: str)`
- `get_runtime_config(set: Settings)`
- Notable constants/configuration names: `T`, `PASSWORD_PLACEHOLDER`, `API_KEY_PLACEHOLDER`, `TIMEZONE_AUTO`, `TIME_FORMAT_12H`, `TIME_FORMAT_24H`, `UI_CONTROL_VISIBILITY_DEFAULTS`, `SETTINGS_FILE`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, network calls, subprocess/runtime control, model calls, WebSocket state, plugin state, settings/state persistence, secret handling, scheduler state.
- Imported dependency areas include: `base64`, `hashlib`, `helpers`, `helpers.notification`, `helpers.print_style`, `helpers.providers`, `helpers.secrets`, `json`, `models`, `os`, `pytz`, `re`, `subprocess`, `typing`.

## Key Concepts

- Important called helpers/classes observed in the source: `TypeVar`, `files.get_abs_path`, `dotenv.get_dotenv_value`, `opts.insert`, `str.strip`, `_is_valid_timezone`, `str.strip.lower`, `_normalize_timezone_setting`, `SettingsOutput`, `get_default_settings`, `_ensure_option_present`, `_resolve_runtime_timezone`, `get_default_secrets_manager`, `get_settings`, `normalize_settings`, `_load_sensitive_settings`, `settings.copy`, `_write_settings_file`, `reload_settings`, `set_settings`, `initialize_agent`.
- Applying settings refreshes active context configs while preserving each subordinate agent's own profile.
- Applying settings starts a deferred `MCPConfig.update(...)` with the current `mcp_servers` string when global MCP server settings change.
- `max_consecutive_unusable_responses` defaults to `5` and controls the cost circuit breaker for malformed or repeated main-model outputs.
- `ui_control_visibility` stores validated mobile and desktop visibility flags for the project selector, clock, connection status, and right canvas rail; missing or malformed values fall back per device.
- The Global default-profile selector lists only globally available profiles.
  A currently configured unavailable profile remains visible with an explicit
  unavailable label so settings can round-trip it truthfully, except that the
  exact `default` utility profile is never offered as a selectable option.
- Legacy settings that still name the hidden `default` utility profile normalize
  to `primary`, so newly created chats always start with a selectable profile.
- `uvicorn_access_logs_enabled` defaults to **True**. Without it a deployment has no record of request rates, status codes or which endpoint produced a 500, and the audit trail does not cover that -- it records decisions, not traffic. Safe to default on only because `helpers/server_startup.py` strips the query string from every access line; turning it on without that would write password-reset tokens into a forwarded log. An existing deployment keeps whatever it has stored: this changes what a new one starts with.
- `set_settings_delta` loads the sensitive fields (`_load_sensitive_settings`) into the stored document before merging. The document holds no passwords or secrets, so merging into it as read and saving wrote empty values over `usr/.env` and `usr/secrets.env`: applying the MCP servers panel cleared the SMTP, RFC and root passwords and every stored secret. `tests/test_settings_delta_keeps_secrets.py` pins it.
- `update_daily_check` defaults to **True**: an appliance's host asks Rezztor's update service once a day which packages follow (ADR 0091, `helpers/update_spool.py`). The Updates page sets it through `api/update_apply.py` (`daily_check`).
- `set_settings_delta` writes the merged document on the connection that holds the `FOR UPDATE` lock, then applies and reloads after the commit. Written in a second transaction after the lock was released, two concurrent saves merged into the same stale row and one panel's change was lost. `tests/test_settings_delta_lock.py` pins it.
- Applying MCP settings prints the parsed servers' names, never the parsed document, which carries their `headers` and `env`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_document_query_plugin.py`
  - `tests/test_download_toast_regressions.py`
  - `tests/test_fasta2a_client.py`
  - `tests/test_mcp_handler_multimodal.py`
  - `tests/test_model_config_api_keys.py`
  - `tests/test_model_config_project_presets.py`
  - `tests/test_oauth_static.py`
  - `tests/test_subagent_profiles.py`

## Child DOX Index

No child DOX files.
