# update_spool.py DOX

## Purpose

- Own the application's side of the appliance's update service (ADR 0080, 0091): read the host tool's state file, store one uploaded update file, write one request, and ask for the daily `refresh`.
- The application verifies, fetches and installs nothing, and calls no update server. `deploy/iso/host/rezztor-update` on the host asks Rezztor's update service, downloads, checks the file's signature itself and installs.

## Ownership

- `status()` — the Updates page's answer: `{"available": false}` with no state file, otherwise `available: true` and the fields in `PUBLIC`, copied from the host's state file: with `updates` (`checked_at`, `server`, `plan`, `licence`, `core`, `available`, `error`), `progress` (`update_all`: `done`, `total`, `current`), `download` (`version`, `received`, `size`) and `os` (ADR 0092: the Debian updates of the last package that carried them, `version`, `snapshot`, `applied`, `packages` of `name`/`from`/`to`, `reboot_needed`, `error`, `at`; `null` before the first). A checked package's `package.os` (`snapshot`, `packages`, `reboot`) and a listed entry's `os` (`packages`, `reboot`) come through as the host wrote them. `updates.available` leaves out packages at or below the installed version, so a list fetched before an install does not offer what is already there.
- `summary()` — `{"phase", "version"}` of an update that is not idle, or `None`; for the bootstrap payload. Never raises.
- `waiting_count()` — the length of that list, or `None` without one; for the bootstrap banner "N updates available". Never raises.
- `store(upload)` — write the upload to `<spool>/inbox/package.rezupd` (a `.part` file, then a rename). When the write fails the `.part` file is removed before `UpdateRefused` is raised.
- `request(action, by, version=None)` — write `<spool>/inbox/request.json` (`id`, `action`, `by`, and the fields below) the same way and return the id. `action` is one of `ACTIONS`: `check`, `install`, `restart`, `discard`, `bundle` (a support bundle), `refresh` (ask the update service what follows), `download` (the first listed package), `update_all` (walk the chain to the latest) and `reboot` (restart the appliance for Debian's updates, ADR 0092).
  - `reboot` is refused here unless the state says `os.reboot_needed`; the host refuses it then too.
  - `refresh`, `download` and `update_all` in a UAT image (`license_client.uat_trust()`) carry `server` = `UAT_SERVER` (`https://update.uat.rezztor.test`) and the baked `ca_pem`; a release image sends neither and its host asks `https://update.rezztor.com` whatever a request says. The host keeps the last UAT server it was given.
  - `download` carries `version`, which must match `VERSION`, be the first entry of the list and be `downloadable`; anything else is refused here before the host refuses it.
  - A Community build (`license_client.community_build()`) is refused `refresh`, `download` and `update_all`: the paid download path is Enterprise (and UAT, which names its own server) only.
- `daily_check_enabled()` — the instance setting `update_daily_check` (`DAILY_SETTING`, `helpers/settings.py`), on by default.
- `daily_refresh(now=None)` — the job loop's daily question (`extensions/python/job_loop/_60_update_check.py`): one `refresh` request, by `daily check`, when a spool exists, the switch is on, the host is not `downloading`, `installing` or `restarting`, no request is pending, and the later of the host's `updates.checked_at` and this process's last ask is a day old, or older than the last install (`installed.updated_at`), so the next update is offered right after one is installed. Returns whether it asked.
- `bundle_path(name)` — the path of a support bundle under `<spool>/state/bundles/`, for `api/support_bundle_download.py`. Only a name that matches the host's pattern and that the host lists in the state; `UpdateRefused` for anything else.
- `UpdateRefused` — a request that is not passed on; its text is shown to the administrator.

## Runtime Contracts

- The spool is `/run/rezztor-update` (`REZZTOR_UPDATE_SPOOL` overrides it for tests): `state/` is mounted read-only from the host, `inbox/` read-write. The Compose file mounts both.
- No state file means no update service (a hand install, a developer's machine): `status()` says so, and `store` and `request` refuse without writing.
- File names in the inbox are fixed. Nothing from the request becomes part of a path, except a bundle's name, and only after it matched the host's pattern and the host's own list.
- The host writes support bundles into the state folder (`rezztor-update`, "the support bundle"): cleaned of secrets there, at most five, none older than seven days. `bundles` and `bundle_error` in the state say what there is and why the last one could not be made.
- `store` and `request` refuse while the host says `downloading`, `installing` or `restarting` (`BUSY`), and while an earlier request has not been taken.
- The update server's address and CA travel only from the image's own UAT trust file, never from the browser.
- The state file is written by root on the host and is trusted as far as showing it goes; only the keys in `PUBLIC` leave this module.
- No secret is read or written here. The update key in the state is the public half; the UAT CA is public material.

## Verification

- `pytest tests/test_update_api.py`.

## Child DOX Index

No child DOX files.
