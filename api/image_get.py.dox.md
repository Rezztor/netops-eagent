# image_get.py DOX

## Purpose

- Own the `image_get.py` API endpoint.
- This module serves allowed image references and fallback file-type icons.
- Keep this file-level DOX profile synchronized with `image_get.py` because this directory is intentionally flat.

## Ownership

- `image_get.py` owns the runtime implementation.
- `image_get.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `ImageGet` (`ApiHandler`)
  - `get_methods(cls) -> list[str]`
  - `async process(self, input: dict, request: Request) -> dict | Response`
- Top-level functions:
- `_resolve_allowed_image_path(path: str) -> str`: Resolve a requested image path and keep it inside Rezztor EAgent's base dir.
- `_set_image_headers(response: Response, filename: str, file_ext: str) -> None`
- `_send_file_type_icon(file_ext, filename=...)`: Return appropriate icon for file type
- `_send_fallback_icon(icon_name)`: Return fallback icon from public directory
- Notable constants/configuration names: `IMAGE_EXTENSIONS`, `SVG_EXTENSIONS`, `SVG_CONTENT_SECURITY_POLICY`.

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- Fails closed. A requested path must resolve (symlinks followed) inside the base directory **and** inside one of: the caller's own `usr/users/<id>/` (uploads, and each chat's images and screenshots), the caller's own folder under the working-folder base (`usr/workdir/<id>/`, or `workdir_path/<id>/` when it is set), or the shared `usr/workdir/documents`. Every other path -- the rest of the application tree, `usr/.env`, `usr/projects`, another account's folder, or any path with no resolvable caller -- is `403` with the same message. A new legitimate root has to be added to `_resolve_allowed_image_path` explicitly. Responses are `Cache-Control: private`.
- A relative path is read first as relative to the caller's own working folder (that is how the Files screen lists names), then as relative to the base directory; either way the result goes through the same check. `tests/test_image_get_security.py`.
- `ImageGet` is an `ApiHandler`.
- `ImageGet` defines `process(...)`.
- `ImageGet` defines `get_methods(...)`.
- Observed side-effect areas: filesystem reads, network calls, subprocess/runtime control, settings/state persistence.
- Imported dependency areas include: `base64`, `helpers`, `helpers.api`, `io`, `mimetypes`, `os`, `pathlib`, `urllib.parse`.

## Key Concepts

- Important called helpers/classes observed in the source: `runtime.is_development`, `Path.resolve`, `candidate.resolve`, `quote`, `_send_fallback_icon`, `files.get_abs_path`, `send_file`, `os.path.splitext.lower`, `os.path.basename`, `Path`, `candidate.is_absolute`, `resolved.relative_to`, `os.path.exists`, `ValueError`, `_set_image_headers`, `_send_file_type_icon`, `files.fix_dev_path`, `_resolve_allowed_image_path`, `files.exists`, `files.get_base_dir`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/test_image_get_security.py`

## Child DOX Index

No child DOX files.
