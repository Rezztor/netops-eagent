# openapi.py DOX

## Purpose

- Own the `openapi.py` API endpoint.
- This module serves the OpenAPI 3.1 document for this instance, generated on request from the live `ApiHandler` classes rather than checked into the repo.
- Keep this file-level DOX profile synchronized with `openapi.py` because this directory is intentionally flat.

## Ownership

- `openapi.py` owns the runtime implementation.
- `openapi.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `OpenApi` (`ApiHandler`)
  - `get_methods(cls) -> list[str]` (`["GET"]`)
  - `required_permission(cls) -> str | None`
  - `async process(self, input: Input, request: Request) -> Output`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`. This is also asserted by `tests/test_api_schema_and_bootstrap.py::test_openapi_endpoint_is_administrator_only`. The document enumerates every endpoint together with the permission each one requires, which is a map of the instance's privileged surface — not secret from someone with repository access, but not handed to an ordinary signed-in user either.
- Delegates entirely to `helpers.api_schema.build_openapi()`; this handler holds no schema-generation logic of its own, only the permission gate, method restriction (`GET`), and an optional version string.
- The version string is best-effort: a failure to read `helpers.git.get_git_info()` is swallowed, since the document is still useful without a version.
- Observed side-effect areas: none — read-only.
- Imported dependency areas include: `typing`, `helpers.api_schema`, `helpers.rbac`, `helpers.api`, `helpers.git` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `api_schema.build_openapi`, `git.get_git_info`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_api_schema_and_bootstrap.py`
  - `tests/test_fastmcp_openapi_security.py`

## Child DOX Index

No child DOX files.
