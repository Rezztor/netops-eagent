# bootstrap.py DOX

## Purpose

- Own the `bootstrap.py` API endpoint.
- One `GET /api/bootstrap` call carries everything a cold frontend needs: session state, version, identity, permissions, runtime flags, CSRF token, display preferences, and the licence summary behind the app-wide banner.

## Ownership

- `Bootstrap` — the handler; `BootstrapResponse` and the nested models are the declared `output_model`, mirrored structurally by `frontend/src/api/types.ts` (pinned by `tests/test_frontend_contract.py`).

## Runtime Contracts

- Callable while signed out by design: the first question a cold frontend has is whether to render the app or the login page, and an anonymous caller gets only `authenticated: false` and the version — nothing about the instance. Also exempt from the forced-password-change gate, so the change-password screen can load.
- Authenticated payload includes `licence`, the minimal verdict every signed-in user can see: `state`, `agent_runs_allowed`, `grace_days_remaining`, `reason`. Deliberately nothing else — seats, customer, and features stay on the admin-gated `license_status` endpoint. `state` is `community`, `licensed`, `grace1`, `grace2` or `restricted`; `reason` is one plain sentence or null. Community reports `community`, runs allowed, and a null reason, so the banner stays down. The payload is `helpers.license_entitlements.licence_summary()`. Bootstrap is reachable in the restricted state, which is exactly when the banner needs to load. Null when the verdict cannot be computed: bootstrap never fails a cold start on a licence problem.
- For holders of `settings.manage` the payload also carries `update`: `{phase, version}` of an appliance update that is checked, being installed or waiting for a restart, or null (no update service, or nothing waits). It is `helpers.update_spool.summary()`: one small file read, and it never fails a cold start (ADR 0080).
- For the same holders, `updates_available`: how many packages Rezztor's update service listed after the running version when the host last asked (ADR 0091), or null without an update service or a list. `helpers.update_spool.waiting_count()`, from the same file, never raises. The app-wide banner says "N updates available".
- No side effects beyond issuing/reusing the session's CSRF token.

## Verification

- `pytest tests/test_api_schema_and_bootstrap.py` (schema parity, anonymous minimality), `tests/test_licence_client.py` (licence summary contents and leak boundary), `tests/test_frontend_contract.py` (the types.ts mirror).

## Child DOX Index

No child DOX files.