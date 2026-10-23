# backup_create.py DOX

## Purpose

- Own the `backup_create.py` API endpoint.
- This module handles backup create requests.
- Keep this file-level DOX profile synchronized with `backup_create.py` because this directory is intentionally flat.

## Ownership

- `backup_create.py` owns the runtime implementation.
- `backup_create.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `BackupCreate` (`ApiHandler`)
  - `requires_auth(cls) -> bool`
  - `requires_loopback(cls) -> bool`
  - `async process(self, input: dict, request: Request) -> dict | Response`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `BackupCreate` is an `ApiHandler`.
- `BackupCreate` defines `process(...)`.
- `BackupCreate` defines `requires_auth(...)`.
- `BackupCreate` defines `requires_loopback(...)`.
- Observed side-effect areas: filesystem writes, filesystem deletion.
- The archive holds every user's data and a database dump, so it must not outlive the download: the handler opens it, removes its temporary directory, and streams from the open handle, which the server closes when the response ends. `response.call_on_close` is not used because `send_file` responses are passthrough and never run those callbacks. The response therefore carries no `Content-Length`; the WebUI reads it as a blob.
- The request must carry `passphrase` (a string of at least 12 characters, in the JSON body, never the URL); it is checked before anything is built, and a missing or short one answers `{success: false, error}`. The download is the encrypted archive, named `<backup_name>.rzbak`, `application/octet-stream`. The passphrase is never logged, audited or echoed.
- A successful create emits `backup.create` with the backup name and pattern counts; never archive bytes or pattern strings.
- Imported dependency areas include: `helpers.api`, `helpers.backup`, `helpers.backup_crypto`, `helpers.persist_chat`, `os`, `shutil`.

## Key Concepts

- Important called helpers/classes observed in the source: `save_tmp_chats`, `BackupService`, `send_file`, `backup_service.create_backup`, `line.strip`, `line.startswith`, `patterns_string.split`, `line.strip.startswith`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- `tests/test_backup_encryption.py` covers the passphrase handling. `tests/test_backup_archive_safety.py` checks that no archive is left on disk once the response exists.

## Child DOX Index

No child DOX files.
