# Cloud executor

The container that runs OpenTofu against Azure for the change plugin (`plugins/_ea_azure_change`). The web application
never runs model-written or template code and never holds an Azure change key; this container does, and answers over a
Unix socket (`/run/cloud-socket/cloud.sock`) to two bearer tokens (job and admin, never the same value).

- **Plans and applies** (`/v1/plan`, `/v1/apply`): a template or checked free code, in a fresh in-memory directory, with
  OpenTofu 1.13 and the `azurerm` and `azapi` providers baked into the image (SHA-256 pinned, no network fetch at run
  time). The **plan digest** is what an engineer approves; apply plans again and runs only that digest, once per change.
- **Never removes** (ADR 0065): `gate.check_plan` refuses any plan that deletes, replaces or forgets, narrows a rule or
  takes a setting away; the runner has no destroy; the ARM client has no delete verb; and the Azure role it runs as
  (`role.json`, checked by `/v1/admin/test`) withholds every delete and stop-like action.
- **Simple actions** (`/v1/action`, `actions.yaml`): start, scale out, tag; seven entries, a closed list.
- **Adoption and drift** (`adopt.py`, `refresh.py`): import existing resources and accept Azure's current state, both
  as the read identity, both only for the approved digest.
- **Administration** (`/v1/admin/*`): the guard rails, the planner and applier keys (made here, sealed at rest, never
  returned), and the role check. Edited from Settings, Azure changes.
- **State** lives in PostgreSQL, encrypted with a passphrase the application does not hold.

Not run against a real tenant yet (test session 2). See `AGENTS.md` for the contracts and tests,
`docs/guides/azure-changes.md` for the administrator's steps, and ADRs 0061 to 0066 and 0068 to 0072.
