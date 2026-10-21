# license_activate.py DOX

## Purpose

- Own the `license_activate.py` API endpoint.
- The write side of the administrator's licence page: `POST /api/license_activate` with `{"action": ...}`. `api/license_status.py` is the read side.

## Ownership

- `LicenseActivate` — dispatches to `helpers/license_client.py`; holds no licence logic of its own.
- Actions: `activate` (`key`: the activation key), `checkin`, `deactivate` (`confirm: true` required), `request_code` (`deployment_name`), `install` (`licence`: the text of an offline licence file, at most 16 KB).

## Runtime Contracts

- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`, and the endpoint is listed in `helpers/api_policy.py` under licence administration.
- **The one place a licence-server call is made from an HTTP request.** It is an administrator's button; a user's request (a chat send, a page load) never waits for the licence server. The call runs with `asyncio.to_thread`.
- The activation key is checked locally (length, alphabet, check character) before any call, and a mistyped key makes none.
- `request_code` makes no network call and stores nothing. `install` verifies the file for this installation before anything is stored.
- A failure returns HTTP 200 with `{"ok": false, "error": <one plain sentence>, "licence": <status>}`; a success `{"ok": true, "licence": <status>}`. `licence` is the payload of `helpers.license_entitlements.licence_status()`, the same shape `license_status` returns.
- **No secret leaves.** The activation key and the installation token are in no answer, no log line and no audit record. Every `LicenceError.reason` is a fixed sentence.
- Each action except `request_code` writes one audit event, `licence.<action>`, with `outcome`, `detail` (the sentence on failure), the licence id and the state.

## Work Guidance

- Add new actions here rather than new endpoints; the page's contract is one endpoint with an `action`.

## Verification

- `pytest tests/test_licence_client.py` — every action through the dispatcher, the permission gate, the audit events, and that neither secret appears in any answer, audit record or output.

## Child DOX Index

No child DOX files.
