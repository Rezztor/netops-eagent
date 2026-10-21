# update_apply.py DOX

## Purpose

- Own the `update_apply.py` API endpoint.
- The write side of the administrator's Updates page: `POST /api/update_apply`.

## Ownership

- `UpdateApply` — `upload` (a form with `action=upload` and the file as `package`), and `install`, `restart`, `discard`, `bundle` (JSON `{"action": ...}`). `bundle` asks the host for a support bundle (Settings, Support bundle); `api/support_bundle_download.py` hands it out.
- From the update service (ADR 0091): `refresh` (Check now), `download` with `version` (the first listed package only), `update_all` (every listed package in order). The host asks the service and downloads; this endpoint writes the request.
- `reboot` (ADR 0092): asks the host to restart the appliance after Debian's updates of a package, and only while the state says `os.reboot_needed` (`update_spool.request` refuses otherwise, and so does the host). The page asks for a confirmation first; agent runs stop while the appliance is away.
- `daily_check` with `enabled` (a boolean): sets the instance setting `update_daily_check` (`settings.set_settings_delta`, not applied to running agents) and answers `{"ok": true, "daily_check": <bool>}`. No request goes to the host.

## Runtime Contracts

- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`, and the endpoint is listed in `helpers/api_policy.py`; auth and CSRF are applied centrally by `helpers/api.py` dispatch.
- The answer is `{"ok": true, "request": "<id>"}` or `{"ok": false, "error": "<sentence>"}`. The id is the one the host puts into the state (`update_status`) when it has taken the request.
- Nothing is installed here. `upload` stores the file in the inbox under a fixed name and asks the host to check it; the others write one request file. The host tool verifies the signature, decides whether the file fits, and does the work (`helpers/update_spool.py`).
- The file name the browser sent is not used. Nothing from the request becomes part of a path. A `download` version is checked against the version pattern and against the first entry of the host's list (`update_spool.request`); the update server and its CA come from the image, never from the browser.
- Every action writes one audit line, `update.<action>`, with the outcome of handing it over (`version` for a download, `enabled` for the daily check). How the update itself ended is in the host's history, which `update_status` returns.
- It works in every licence state, including restricted.

## Verification

- `pytest tests/test_update_api.py`.

## Child DOX Index

No child DOX files.
