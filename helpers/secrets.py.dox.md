# secrets.py DOX

## Purpose

- Own the `secrets.py` helper module.
- This module loads, aliases, masks, and streams secret values safely.
- Keep this file-level DOX profile synchronized with `secrets.py` because this directory is intentionally flat.

## Ownership

- `secrets.py` owns the runtime implementation.
- `secrets.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `EnvLine` (no explicit base class)
- `StreamingSecretsFilter` (no explicit base class)
  - `process_chunk(self, chunk: str) -> str`
  - `finalize(self) -> str`
- `SecretsManager` (no explicit base class)
  - `get_instance(cls, *secrets_files) -> 'SecretsManager'`
  - `read_secrets_raw(self) -> str`
  - `load_secrets(self) -> Dict[str, str]`
  - `save_secrets(self, secrets_content: str)`
  - `save_secrets_with_merge(self, submitted_content: str)`: every value is written through `helpers.dotenv._quote`, so a secret holding `"`, `\` or a line break reads back as it was stored instead of turning its line into one the reader cannot parse
  - `get_keys(self) -> List[str]`
  - `get_secrets_for_prompt(self) -> str`
  - `create_streaming_filter(self) -> 'StreamingSecretsFilter'`
- Top-level functions:
- `alias_for_key(key: str, placeholder: str=...) -> str`
- `get_secrets_manager(context: 'AgentContext|None'=...) -> SecretsManager`
- `get_project_secrets_manager(project_name: str, merge_with_global: bool=...) -> SecretsManager`
- `get_default_secrets_manager() -> SecretsManager`
- `tool_resolves_secrets(tool_name: str) -> bool`
- Notable constants/configuration names: `ALIAS_PATTERN`, `DEFAULT_SECRETS_FILE`, `_RUNTIME_CREDENTIAL_KEYS`, `SECRET_RESOLVING_TOOLS`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- The agent-facing `get_secrets_manager` masks and unpacks `API_KEY_*` and login/password credentials from `usr/.env`, every value from the global `usr/secrets.env`, and every value from the active project's `secrets.env`; ordinary runtime settings are not treated as secrets. `get_default_secrets_manager` remains scoped to the single writable `usr/secrets.env` file.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, WebSocket state, settings/state persistence, secret handling.
- Imported dependency areas include: `dataclasses`, `dotenv.parser`, `helpers`, `helpers.errors`, `helpers.extension`, `io`, `os`, `re`, `threading`, `time`, `typing`.
- Instance secrets (`usr/secrets.env`, and the `API_KEY_*` and runtime credentials of `usr/.env`) resolve from an alias only when the running conversation's owner holds `settings.manage` (`_instance_secrets_allowed`, read from the pushed user scope; no owner is refused). For any other account `replace_placeholders` raises `RepairableException` ("not available to this account") and the tool does not run. A project's own `secrets.env` resolves only for an account holding `tools.code_execution` (`_project_secrets_allowed`); a standard user resolves, and is shown the names of, no secret at all. Masking of values in output is unchanged and applies to everyone. `tests/test_secrets.py` covers this. `resolvable_secrets()` is the one rule for what an account may resolve; `replace_placeholders` (including its "available secrets" error) and `get_secrets_for_prompt` (the alias names written into the system prompt) both use it, so an account that cannot resolve an alias is not told its name.

- Aliases resolve only into the arguments of the tools in `SECRET_RESOLVING_TOOLS` (`code_execution_tool`, `code_execution_remote`, `input`, `text_editor`, `text_editor_remote`), asked through `tool_resolves_secrets` by `extensions/python/tool_execute_before/_10_unmask_secrets.py`. Every other tool gets the alias as written and the agent a one-line warning: their arguments leave the host, persist where another account reads them, or reach another agent. It is an allow-list, so a new tool resolves nothing until it is added here deliberately. ADR 0086; `tests/test_secrets.py` covers the document-URL proof and the code-execution path.

## Key Concepts

- Important called helpers/classes observed in the source: `key.upper`, `placeholder.format`, `SecretsManager.get_instance`, `self._replace_full_values`, `self._longest_suffix_prefix`, `threading.RLock`, `join`, `files.write_file`, `self._invalidate_all_caches`, `self.load_secrets`, `self.read_secrets_raw`, `self.parse_env_lines`, `self._serialize_env_lines`, `StreamingSecretsFilter`, `re.sub`, `self.parse_env_content`, `parse_stream`, `AgentContext.current`, `projects.get_context_project_name`, `files.get_abs_path`, `dotenv.get_dotenv_file_path`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_secrets.py`
  - `tests/test_plugin_scan_prompt.py`
  - `tests/test_print_style.py`
  - `tests/test_time_travel.py`

## Child DOX Index

No child DOX files.
