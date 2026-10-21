# message.py DOX

## Purpose

- Own the `message.py` API endpoint.
- This module submits a user message and runs agent processing synchronously through the UI API.
- Keep this file-level DOX profile synchronized with `message.py` because this directory is intentionally flat.

## Ownership

- `message.py` owns the runtime implementation.
- `message.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `Message` (`ApiHandler`)
  - `async process(self, input: dict, request: Request) -> dict | Response`
  - `async respond(self, task: DeferredTask, context: AgentContext)`
  - `async communicate(self, input: dict, request: Request)`

## Runtime Contracts

- HTTP handlers must derive from `helpers.api.ApiHandler`; WebSocket handlers must derive from `helpers.ws.WsHandler`.
- Update this file whenever request payloads, authentication or CSRF requirements, response shapes, route side effects, or WebSocket event contracts change.
- `Message` is an `ApiHandler`.
- Accepts JSON or `multipart/form-data` only; any other body answers 415 with a JSON `error` (UAT F26).
- A turn that fails answers 502 with `{"ok": false, "error": <one plain sentence from errors.user_message>, "context": id}`, never a 500 carrying the exception text, which for a runner failure names a slot and a server path (UAT F29). `MessageAsync` inherits the 415 rule; it does not wait for the turn.
- An empty or missing `context` starts a new conversation under a fresh id; it never resolves to the caller's earlier chat (UAT F30).
- `Message` defines `process(...)`.
- Multipart attachments are saved to the account's uploads folder only after the account quota is checked for all of them together (`helpers.file_browser.check_account_quota`, the same quota as `api/upload.py`). Over it, `QuotaExceeded` is raised before anything is saved, and the dispatcher answers 413. The check and the saves run under `file_browser.quota_lock()`, so concurrent messages cannot each pass against the same free space. `tests/test_account_storage_quota.py`.
- Observed side-effect areas: filesystem reads, filesystem writes, settings/state persistence, scheduler state.
- Imported dependency areas include: `agent`, `helpers`, `helpers.api`, `helpers.defer`, `helpers.security`, `os`.

## Key Concepts

- Important called helpers/classes observed in the source: `request.content_type.startswith`, `self.use_context`, `mq.log_user_message`, `self.communicate`, `self.respond`, `task.result`, `request.files.getlist`, `files.get_abs_path`, `request.get_json`, `extension.call_extensions_async`, `context.communicate`, `os.makedirs`, `UserMessage`, `safe_filename`, `attachment.save`, `context.get_agent`, `os.path.join`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve authentication, CSRF, loopback, and API-key checks unless the endpoint contract explicitly changes.
- Update frontend callers, plugin callers, and tests together when payload shape changes.
- Use `helpers.api.Response` for non-JSON responses, files, redirects, or status-specific replies.

## Verification

- Run endpoint-specific or API/WebSocket tests for changed behavior; smoke-test browser callers when no focused test exists.
- Related tests observed by source search:
  - `tests/email_parser_test.py`
  - `tests/rate_limiter_test.py`
  - `tests/test_api_chat_lifetime.py`
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_chat_compaction.py`
  - `tests/test_docker_release_plan.py`
  - `tests/test_document_query_fallback.py`
  - `tests/test_download_toast_regressions.py`

## Child DOX Index

No child DOX files.
