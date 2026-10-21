# dotenv.py DOX

## Purpose

- Own the `dotenv.py` helper module.
- This module loads and updates `.env` configuration values.
- Keep this file-level DOX profile synchronized with `dotenv.py` because this directory is intentionally flat.

## Ownership

- `dotenv.py` owns the runtime implementation.
- `dotenv.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `load_dotenv()`
- `get_dotenv_file_path()`
- `get_dotenv_value(key: str, default: Any=...)`
- `save_dotenv_value(key: str, value: str, reload_env: bool=...)`: writes one line per key; a value with anything beyond `[A-Za-z0-9_@%+=:,./-]` is double-quoted with `\\`, `"`, newline, CR and tab escaped, so `#`, spaces, quotes and newlines round-trip and can not add a second key. `load_dotenv` reads with interpolation off, so `${...}` in a value is literal. Every text is checked to read back (`_reads_back`, with a quoted line after it) before it is written: the reader takes a final `\\"` for an escaped quote, so a value ending in a backslash is written bare instead, and one that neither spelling can hold raises `ValueError` and leaves the file as it was. `tests/test_dotenv_value_origin.py` covers this.
- Notable constants/configuration names: `KEY_AUTH_LOGIN`, `KEY_AUTH_PASSWORD`, `KEY_RFC_PASSWORD`, `KEY_ROOT_PASSWORD`, `KEY_AZURE_CLIENT_SECRET`, `KEY_AZURE_CLIENT_CERT` (the Azure plugin's credential; only `plugins/_ea_azure/helpers/credential.py` reads them, and they are never `API_KEY_*` names), `KEY_CLOUD_JOB_TOKEN` (`REZZTOR_CLOUD_JOB_TOKEN`: the bearer token the Azure change plugin sends the cloud executor; also read from `REZZTOR_CLOUD_JOB_TOKEN_FILE` by the plugin's `executor_client`; never an `API_KEY_*` name, never returned by an endpoint or tool).

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem reads, filesystem writes, secret handling.
- Imported dependency areas include: `dotenv`, `files`, `os`, `re`, `typing`.
- `save_dotenv_value(..., reload_env=False)` updates the persisted file without changing the running process environment.
- `save_dotenv_value` holds one lock for its read, change and write, and replaces the file whole (`os.replace` of a `0600` temporary file in the same folder): two requests saving at once cannot lose each other's key, and a reader never sees the file half written. It is the only writer of `usr/.env`.

## Key Concepts

- Important called helpers/classes observed in the source: `_load_dotenv`, `get_abs_path`, `os.getenv`, `get_dotenv_file_path`, `load_dotenv`, `os.path.isfile`, `f.readlines`, `f.seek`, `f.writelines`, `f.truncate`, `f.write`, `re.match`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/email_parser_test.py`
  - `tests/test_model_config_api_keys.py`
  - `tests/test_timezone_regressions.py`

## Child DOX Index

No child DOX files.
