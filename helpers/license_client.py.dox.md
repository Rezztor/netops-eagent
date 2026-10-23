# license_client.py DOX

## Purpose

- Own the `license_client.py` helper module: what this installation holds of its licence, what it concludes from it, and its calls to the licence server.
- Implements the product side of the licence contract (the licence server's formats and protocol, in the code of `rezlic/` and of this module) on top of `rezlic/`, which holds the formats and is never edited here. ADR 0078 records the switch.

## Ownership

- `ROOT_KEYS` — the whole list of root public keys this build trusts, `{"root-2026a": ...}`. A constant. No setting, environment variable or file adds or replaces a key. A UAT image replaces it whole with its baked roots (`uat_trust()`, ADR 0085).
- `DEFAULT_SERVER` (`https://license.rezztor.com`) and `ENV_SERVER` (`REZZTOR_LICENCE_SERVER_URL`, development and tests only).
- `state(now=None) -> dict` — the verdict (`community`, `licensed`, `grace1`, `grace2`, `restricted`), its `reason` code and plain `message`, the verified licence and lease payloads, the time used, when the installation becomes restricted, and how the last call went. `releasing` is the id of a previous licence whose release is still pending, or None. Holds no secret. A Community build answers without reading the database.
- `activate(key_text) -> str | None`, `checkin()`, `deactivate()`, `load_licence_file(text)`, `request_code(deployment_name)` — the administrator's actions. Each raises `LicenceError(reason)` when it does not complete. `activate` returns the id of a different licence it replaced, when that release was confirmed or is still pending, and None otherwise.
- `tick(now=None)` — the background half, called once a minute by `extensions/python/job_loop/_30_license_heartbeat.py`.
- `installation_id()`, `product_version()`, `seats_used()`, `server_url()`.
- `devices_used() -> int` and `DEVICE_CLOUD_TYPES` (ADR 0093) — active `inventory_device` rows plus `cloud_resource` rows not removed whose `type` is one of the nine Azure firewall, load balancer and gateway types. Two counts in one statement through `db.transaction(db.NO_SCOPE)`; both tables are installation-wide with no row-level policy.
- `LicenceError` — `reason` is one fixed plain sentence; `final` marks an answer that says the server did not act (400, 403); `status` is the HTTP status when the server answered with one.

## Runtime Contracts

- **Storage.** One row of `licence_state` (migration `0024_licence_state.sql`), read and written through `db.transaction(db.NO_SCOPE)`: the table is installation-wide and has no row-level policy. The row is created by the first write. It is part of a backup like any other table.
- **Nothing is believed because it is stored.** `state()` builds a fresh `TrustStore` from `ROOT_KEYS`, accepts the stored certificates again, and verifies the stored licence (for this installation id) and lease every time. A stored document that fails gives `restricted` with reason `invalid` and the rule's code in `problem`.
- **The calls.** `POST /v1/activate`, `/v1/checkin`, `/v1/deactivate` and `GET /v1/keys`, with `User-Agent: RezztorEAgent/<version>`, a 15 second timeout, bodies of at most 4 KB sent with sorted keys, answers of at most 64 KB, and no redirect followed. Standard library only.
- **A request is saved before it is sent** (`pending`) and repeated unchanged, nonce included, until a verified answer arrives; it survives a restart because it lives in the table. A new administrator action replaces it. A pending activation or deactivation that the server refuses (400, 403) is dropped, so the activation key is not kept waiting; a refused check-in stays and is repeated.
- **An answer is verified whole before any of it is stored**, and then stored with the cleared `pending` in one statement. Until then the licence, lease and token held stay. Any other answer (400, 403, 429, 5xx, a redirect, no answer, a body that is not the protocol's, a document that fails verification) is recorded in `last_result` and changes nothing else.
- **Replacement** follows the reference client: a licence in an answer must be a replacement of the one held (same id, same or higher version, same class of signer) unless it arrives with an activation; an offline file for the id held must be a replacement too.
- **Deactivation** acts on the one unsigned answer, `{"ok": true}`, and only against itself: the lease and the token are discarded and the installation is then restricted (no lease).
- **A switch.** When `activate` succeeds for a different `lic` than the one held, and a token for the old one exists, the new licence, lease and token are stored first. Then the old activation is released with the same `POST /v1/deactivate` body `deactivate` sends (`lic`, `iid`, `token`, `nonce`), using the old token. That request is saved before it is sent and occupies `pending` until the server confirms; `tick` repeats it unchanged, including across a restart. A refusal (400, 403) drops it, as a refused deactivation does, and is not retried. The new lease and token are not discarded by that release. Re-activating the same `lic` releases nothing. A Community build never reaches this: `activate` raises before any call. The release writes `licence.deactivate` (outcome, detail, the previous licence id, the state) when the server confirms it or refuses it, and not on a failure that will be retried. The token is not in the event. `state()` exposes the previous id as `releasing` while the release is pending, and nothing else of the request.
- **An offline licence** (`load_licence_file`) must be an offline licence bound to this installation id. Loading one discards the lease, the token and any pending request. An offline installation makes no call: `tick` finds nothing to check in with.
- **Community.** `community_build()` is true unless `enterprise_build()` is (the stamp says `"licence": "enterprise"`) or `uat_trust()` is not None. `state()` then answers `community` and reads no database. `tick`, `activate`, `checkin`, `deactivate`, `load_licence_file` and `request_code` return or raise before any call and before any write. No setting, environment variable, row or request switches it.
- **The clock.** `tick` raises the mark to the clock when the clock has passed it by ten minutes; an accepted lease sets the mark to the lease's `now`. `state()` passes the mark to `rezlic.verdict`, which uses it as the time when the clock reads more than an hour behind it and reports `clock_behind`.
- **The schedule.** After a success the next call is due a day later; after failures the waits are 1, 5, 30 minutes, then hourly (`RETRY_WAITS`). A clock set back before the last attempt does not hold a call back. A check-in sends the number of enabled accounts as `seats` and `devices_used()` as `devices`.
- **A server that does not know `devices`** (ADR 0093). A licence server from before it answers a check-in with an extra field with 400 (422 is treated the same). Then, in the same attempt, the check-in is sent once more without `devices`, under a new nonce, and is the pending request from then on; `devices` is left out of new check-ins for `DEVICES_RETRY` (seven days) after the refusal (`_devices_refused_at`, in this process only). Any other failure repeats the body unchanged.
  - **The fallback is said, once.** The refusal is quiet in the product's own words — the check-in goes again without the count and nothing else changes — so without a line in the log the appliance looked as if it had never told the server how many devices it has. Two lines at INFO on the module's logger (`helpers.license_client`, stdout, the stream ADR 0005 ships the audit events to): one when the fallback starts, naming the server's status code and the UTC moment until which the count is left out (`_log_devices_refused`), and one when the count is sent with the check-ins again after the week (`_log_devices_sent_again`). No request body, no installation token and no device count reach a line. The second line is written once per process (`_devices_sent_again_at`), not once a day.
- **Server address.** `https`, or `http` to `localhost`, `127.0.0.1` or `::1`; anything else is refused. The installation token is therefore sent only over TLS, except to this machine in development.
- **Secrets.** The installation token and the activation key are in the table and in request bodies and nowhere else. Nothing here logs a body; every `LicenceError.reason` is built without them.
- One lock serialises the actions and `tick` inside the web process. The network call is blocking: callers on an event loop run it with `asyncio.to_thread`.
- Imported dependency areas include: `rezlic`, `helpers.db`, `helpers.runtime`, `helpers.git`, `helpers.users`, `urllib`, `logging`.

## Work Guidance

- Change the formats in the licence server and copy `rezlic/` again; do not work around them here.
- Keep `state()` free of secrets: `helpers/license_entitlements.py` passes parts of it to every signed-in user.
- A new stored field needs a migration, an entry in `_EMPTY`, and, if it is JSON, in `_JSON`.

## Verification

- `pytest tests/test_licence_client.py` — a stand-in licence server over HTTP on 127.0.0.1, through a whole life: activation, forty days of check-ins, a lost answer, a renewal, revocation, grace, restriction, release, a switch to another licence (the old token released, a same-licence activation not released, a failed release retried by `tick` from the table, a refusal dropped), a clock set back, forged and misdirected answers, offline files, a server that answers 500, garbage or nothing, and a server that refuses `devices` (and the two log lines the refusal and the week after it write); and the device count against real rows. Needs PostgreSQL.
- `pytest tests/test_rezlic_vectors.py` — the formats.

## Child DOX Index

No child DOX files.

## Community and Enterprise

`enterprise_build()` (cached) is true only when `build_info.json` has `"licence": "enterprise"`. A release build passes
`BUILD_LICENCE=enterprise`. A source checkout has no stamp. A self-built image and the old open lab stamp
(`"licence": "open"`) do not say enterprise.

`community_build()` is true unless that stamp is enterprise or the image is UAT (`uat_trust()` is not None, including a
broken UAT image that trusts nothing). `state()` then answers `community` with no licence document and reads no
database. `tick` does nothing. The administrator's actions raise `LicenceError` before any call. Verified by the
Community section of `tests/test_licence_client.py`.

Enterprise is today's verdict: licence, lease, grace, restricted, device counts. The licence tests set an enterprise
stamp so that path stays what it was.

## UAT build (ADR 0085)

`uat_trust()` (cached) is not None only in an image whose stamp has `"licence": "uat"` and a version that ends in
`-uat`, or whose stamp cannot be read (missing, not JSON, not an object) while `licence_trust.json` lies beside it:
that broken UAT image gets the trust-nothing result, never `ROOT_KEYS` and `license.rezztor.com`. It then reads `licence_trust.json` (`UAT_TRUST_FILE`, beside the stamp, written by the image build from
`BUILD_UAT_TRUST`): `root_keys`, `server`, `ca_pem` and, optionally, `update_server` (the update service of that build, which `helpers/update_spool.py` sends to the host). With it, `_roots()` is those keys and not `ROOT_KEYS`, the
baked `server` is the default of `server_url()` (`REZZTOR_LICENCE_SERVER_URL` still overrides), and `_transport()`
verifies TLS against `ca_pem` alone (a client context that starts with no CA). A missing or wrong file, a key that
does not load, or a root equal to a production one gives empty roots and no server: the image trusts nothing and
never falls back to `ROOT_KEYS`. In any other build `_roots()` is `ROOT_KEYS` and `_transport()` is the module's
`_opener`, as before. Verified by the UAT section of `tests/test_licence_client.py`, which includes a stand-in
server over HTTPS with a test CA.
