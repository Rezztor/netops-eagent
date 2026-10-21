# license_entitlements.py DOX

## Purpose

- Own the `license_entitlements.py` helper module.
- The surface other code asks about the licence, and the definition of what a Restricted installation does not start. Everything is derived from `helpers.license_client.state()`.

## Ownership

- `require_agent_runs_allowed() -> None` — raises `LicenceRestrictedError(sentence)` when the installation is restricted. Called at `AgentContext.communicate` and at the scheduler's task runner (`helpers/task_scheduler.py`).
- `restricted_refusal(state=None) -> str | None` — the sentence every refusal uses, or None. It names the licence page.
- `agent_runs_allowed() -> bool`, `is_licensed() -> bool`.
- `seats_used() -> int` (enabled accounts), `seats_allowed(state=None) -> int | None`.
- `devices_used() -> int` (`license_client.devices_used()`), `devices_allowed(state=None) -> int | None`, `devices_warning(used, allowed) -> "near" | "over" | None`, and `EDITION_DEVICES` (imported from `rezlic.formats`, the one table the licence server, the home signer and the product read: free 100, pro and ultra 500; ADR 0093).
- `feature_enabled(name) -> bool` — whether the licence's `features` list holds the name, in an honoured state.
- `licence_summary() -> dict` — what every signed-in user may know (`state`, `agent_runs_allowed`, `grace_days_remaining`, `reason`); used by `api/bootstrap.py`.
- `licence_status() -> dict` — what the administrator's licence page shows; used by `api/license_status.py` and `api/license_activate.py`.
- `HONOURED_STATES = ("licensed", "grace1", "grace2")`.
- `FEATURE_PLUGINS` — licence feature name to bundled plugin: `network_automation` `_ea_ansible`, `azure` `_ea_azure`, `azure_change` `_ea_azure_change`, `solarwinds` `_ea_solarwinds`, `siem_forward` `_ea_siem_forward`, `mail` `_ea_mail`, `auto_skills` `_ea_auto_skills`, `attachments` `_ea_attachments`.
- `RESTRICTED_TOOLS` — the agent tools that draft or apply a device or cloud change.

## Runtime Contracts

- **States.** `licensed`, `grace1`, `grace2`: full function. `restricted`: sign-in, reading, export, settings and the licence page work; new agent runs, new device or cloud changes and scheduled tasks do not start. Nothing is deleted and nothing in flight is killed. An installation with no licence, or with a stored licence that does not verify, is restricted.
- **Where Restricted is applied.** `require_agent_runs_allowed()` at the two run chokepoints; `RESTRICTED_TOOLS` at `extensions/python/tool_execute_before/_05_licence_restricted.py`; `helpers.api_policy.REFUSED_WHEN_RESTRICTED` at the API dispatcher. A new way to start a run or a change must be added to one of the three.
- `LicenceRestrictedError` is mapped to a 403 with the sentence in `helpers/api.py`; the scheduler records it as the task's failure.
- **Features are shown, not enforced.** `feature_enabled()` answers from the licence and nothing calls it yet; whether a plugin whose feature is absent is refused is an open decision recorded in ADR 0078. `_ea_inventory` and `_ea_interaction` have no feature name.
- **Seats are shown, not enforced.** Account creation does not ask.
- **Devices are shown and warned about, not enforced** (ADR 0093). `devices_allowed()` is None outside an honoured state; else the licence's signed `devices` (rezlic verified it is an int >= 1), else None for a trial, else `EDITION_DEVICES` by edition (None for `standard`, `plus`, `lab` and any other). `devices_warning()` is `over` above the limit, `near` from 90% of it, else None. Nothing asks either before an approval or a run.
- `licence_status()` carries `devices_used`, `devices_allowed` and `devices_warning` beside `seats_used`.
- `licence_status()` and `licence_summary()` never carry the installation token, the activation key or a request body. `licence_summary()` carries no commercial term (no customer, seats or features).
- No side effects and no network call: one row read and a few signature verifications per call.
- Update this file when either payload or the meaning of Restricted changes; `frontend/src/api/licence.ts`, `frontend/src/api/types.ts` and the two endpoints move in the same change.

## Verification

- `pytest tests/test_licence_client.py` — the gates (run start, change tools, dispatcher), the features, the device limit and its warnings, both payloads and that no secret is in them.
- Other tests run on a licensed installation through the `licensed_installation` fixture in the root `conftest.py`.

## Child DOX Index

No child DOX files.
