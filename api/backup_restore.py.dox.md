# backup_restore.py DOX

## Purpose

- Own the `backup_restore.py` API endpoint.
- This module handles backup restore requests.
- Keep this file-level DOX profile synchronized with `backup_restore.py` because this directory is intentionally flat.

## Ownership

- `backup_restore.py` owns the runtime implementation.
- `backup_restore.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `BackupRestore` (`ApiHandler`)
  - `requires_auth(cls) -> bool`
  - `requires_loopback(cls) -> bool`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `BackupRestore` is an `ApiHandler`.
- `BackupRestore` defines `process(...)`.
- `BackupRestore` defines `requires_auth(...)`.
- `BackupRestore` defines `requires_loopback(...)`.
- Observed side-effect areas: filesystem writes, filesystem deletion, settings/state persistence.
- Imported dependency areas include: `helpers.api`, `helpers.backup`, `helpers.persist_chat`, `json`, `plugins._audit.helpers.audit`, `werkzeug.datastructures`.
- Archive entries that would land outside `usr/` are never written; they come back in `skipped_files` with `reason: "refused"` and a `detail`.
- Emits one `backup.restore` audit event per request that reaches the restore: `outcome=failure` with a capped `error` when restore raises; otherwise `outcome=success` with `archive` (the uploaded file name), `archive_sha256`, `overwrite_policy`, `clean_before_restore`, `restored`/`deleted`/`refused`/`errors` counts, up to 20 `refused_entries`, and `database_restored`. Audit failures never fail the restore.

- The multipart form may carry `passphrase` (never the URL). It is required when the uploaded file is an encrypted backup (`.rzbak`); a missing or wrong one, or a damaged file, answers `{success: false, error}` with one plain message and writes nothing. A plain `.zip` made before backups were encrypted still works. The response carries `encrypted: bool` so the UI can say an old backup was not protected. The passphrase is never logged or audited.

## Key Concepts

- Important called helpers/classes observed in the source: `request.form.get.lower`, `json.loads`, `BackupService`, `load_tmp_chats`, `backup_service.restore_backup`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- `tests/test_backup_archive_safety.py` drives this handler with a crafted archive and checks the refusal and the audit event; `tests/test_audit_logging.py` requires the event name.

## Child DOX Index

No child DOX files.
