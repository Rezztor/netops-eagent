# license_status.py DOX

## Purpose

- Own the `license_status.py` API endpoint.
- Serves the installation's licence verdict to the administrator's licence page: `GET/POST /api/license_status` → `{"ok": true, "licence": {...}}`, the payload of `helpers.license_entitlements.licence_status()`.

## Ownership

- `GetLicenseStatus` — read-only.

## Runtime Contracts

- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`, and the endpoint is listed in `helpers/api_policy.py`; auth and CSRF are applied centrally by `helpers/api.py` dispatch.
- No side effects and no network call: the verdict is worked out from the signed documents the installation holds.
- The payload carries the state, its reason and plain message, the licence's customer, edition, features, seats and contract end, the lease end, seats in use, network devices in use with the licence's device limit and a `devices_warning` (`near`, `over` or null; nothing is refused, ADR 0093), how the last call to the licence server went, and the installation id. It never carries the installation token or the activation key. A Community build's state is `community`, its message is the one line the page shows, and `devices_allowed` and `devices_warning` are null.
- It works in every state, including restricted.

## Verification

- `pytest tests/test_licence_client.py`.

## Child DOX Index

No child DOX files.
