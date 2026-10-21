# api_schema.py DOX

## Purpose

- Own the `api_schema.py` helper module.
- This module generates an OpenAPI 3.1 document from the `ApiHandler` classes discovered under `api/`, `plugins/<name>/api/`, and `usr/api/`, since the API has no route table other than the filesystem.
- Keep this file-level DOX profile synchronized with `api_schema.py` because this directory is intentionally flat.

## Ownership

- `api_schema.py` owns the runtime implementation.
- `api_schema.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Types:
- `EndpointSpec` (`TypedDict`): everything introspectable about one API endpoint (path, source, handler, methods, auth/CSRF/API-key/loopback flags, required permission and its source, input/output models).
- Top-level functions:
  - `_handler_classes(path: Path)`: load the `ApiHandler` subclasses defined in one file, or `[]` on import failure.
  - `_summary_for(handler_cls: type) -> str`: first paragraph of the handler's docstring.
  - `_optional_model(handler_cls: type, name: str)`: call an optional `input_model`/`output_model` classmethod, or `None`.
  - `_spec_for(handler_cls, endpoint: str, source: str, plugin: str, module: Path) -> EndpointSpec`
  - `collect_endpoints() -> list[EndpointSpec]`: walk core, plugin, then `usr/api/` directories in dispatch-precedence order; a duplicate endpoint name is reported and the first occurrence kept.
  - `_model_schema(model, components: dict) -> dict | None`: inline a pydantic model's JSON Schema, hoisting `$defs` into `components`.
  - `_operation(spec: EndpointSpec, method: str, components: dict) -> dict`: build one OpenAPI operation object, including security requirements and `x-*` extensions.
  - `build_openapi(version: str = "") -> dict`: assemble the full OpenAPI document, including `x-permissions` (the role/permission catalogue).
- Constants: `OPENAPI_VERSION`, `SCHEMA_REVISION`, `_SECURITY_SESSION`, `_SECURITY_CSRF`, `_SECURITY_API_KEY`.

## Runtime Contracts

- Endpoints are enumerated with `helpers.modules.list_modules`, which names a handler by its `.py` path.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever the document's shape changes in a way a consumer would notice — bump `SCHEMA_REVISION` alongside the code change.
- `build_openapi()` is the sole backend for `api/openapi.py`, which is administrator-only (`rbac.PERM_SETTINGS_MANAGE`).
- A handler's permission is resolved as: its own `required_permission()` classmethod if declared (`permission_source: "handler"`), else `helpers.api_policy.required_permission_for(endpoint)` (`"policy"`), else `None` (`"none"`).
- `session_required` (and therefore the `sessionCookie` security scheme) is true when `requires_auth()` is true **or** a permission is required — a handler can decline `requires_auth()` and still need a session, because `required_permission()` checks authentication itself before checking the permission.
- Request/response bodies are documented precisely only for handlers that declare `input_model()`/`output_model()` (pydantic models); everything else is emitted as a free-form `{"type": "object", "additionalProperties": true}` schema and marked `x-body-schema: "free-form"`.
- A handler file that fails to import is skipped with a `PrintStyle.warning`, not raised — one broken plugin handler must not take the schema endpoint down.
- Plugin API directories are walked with `rglob`, not `glob`, deliberately: `_dispatch` in `helpers/api.py` splits the endpoint path with `maxsplit=2`, so a handler may itself live under a nested path (e.g. `plugins/_ea_connector/api/v1/<name>.py`).
- Observed side-effect areas: none — read-only introspection over the filesystem and loaded classes; it does not write.
- Imported dependency areas include: `inspect`, `pathlib`, `typing`, `helpers.api_policy`, `helpers.files`, `helpers.rbac`, `helpers.print_style`, `helpers.api` (deferred), `helpers.modules` (deferred), `helpers.plugins` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `load_classes_from_file`, `inspect.getdoc`, `handler_cls.required_permission`, `api_policy.required_permission_for`, `module.relative_to`, `handler_cls.get_methods`, `handler_cls.requires_auth`, `handler_cls.requires_csrf`, `handler_cls.requires_api_key`, `handler_cls.requires_loopback`, `plugins.get_plugin_roots`, `model.model_json_schema`, `components.setdefault`, `git.get_git_info`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- A new handler opts in to a documented body schema by implementing `input_model()`/`output_model()`; do not invent shapes here that would drift from the code.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`
  - `tests/test_legacy_ui_removal_readiness.py`

## Child DOX Index

No child DOX files.
