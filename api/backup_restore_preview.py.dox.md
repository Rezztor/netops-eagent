# backup_restore_preview.py DOX

## Purpose

- Own the `backup_restore_preview.py` API endpoint.
- This module handles backup restore preview requests.
- Keep this file-level DOX profile synchronized with `backup_restore_preview.py` because this directory is intentionally flat.

## Ownership

- `backup_restore_preview.py` owns the runtime implementation.
- `backup_restore_preview.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `BackupRestorePreview` (`ApiHandler`)
  - `requires_auth(cls) -> bool`
  - `requires_loopback(cls) -> bool`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `BackupRestorePreview` is an `ApiHandler`.
- `BackupRestorePreview` defines `process(...)`.
- `BackupRestorePreview` defines `requires_auth(...)`.
- `BackupRestorePreview` defines `requires_loopback(...)`.
- Observed side-effect areas: filesystem deletion, settings/state persistence.
- Imported dependency areas include: `helpers.api`, `helpers.backup`, `json`, `werkzeug.datastructures`.
- Uses the same gate as restore: entries restore would refuse are reported in `skipped_files` with `reason: "refused"` and never appear in `files_to_restore`, and `files_to_delete` never names anything outside `usr/`.

- The multipart form may carry `passphrase` (never the URL). It is required when the uploaded file is an encrypted backup (`.rzbak`); a missing or wrong one, or a damaged file, answers `{success: false, error}` with one plain message and writes nothing. A plain `.zip` made before backups were encrypted still works. The response carries `encrypted: bool` so the UI can say an old backup was not protected. The passphrase is never logged or audited.

## Key Concepts

- Important called helpers/classes observed in the source: `request.form.get.lower`, `json.loads`, `BackupService`, `backup_service.preview_restore`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- `tests/test_backup_archive_safety.py` covers the preview's refusals and its deletion list.

## Child DOX Index

No child DOX files.
