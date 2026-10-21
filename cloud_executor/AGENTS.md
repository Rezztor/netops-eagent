# Cloud executor DOX

## Purpose

- Own the container that runs OpenTofu against Azure for the change plugin (`plugins/_ea_azure_change`): its API, the
  static gate and plan policy, the runner, the keys, the template catalogue loader, and the image.
- Contract: `docs/plans/2026-10-03-azure-operations-build.md` ("Cloud executor API", "The static gate and the plan
  policy", "The Azure role for key C", "Templates") and `docs/plans/2026-10-04-azure-change-set-build.md` ("Executor (W1)");
  ADRs 0061 to 0066, 0068, 0070 to 0072, 0074, 0075.

## Ownership

- `app.py` the API on a Unix socket; `runner.py` plan, apply, verify; `gate.py` code check, plan policy, digest, summary
  (the application imports the same file); `keys.py`, `rolecheck.py` + `role.json`, `rails.py`, `cost.py`, `store.py`,
  `arm.py`, `db.py`; `templates.py` + `templates.yaml` + `templates/` (the loader is this directory's, the entries are
  the template workers'); `actions.py` + `actions.yaml` the simple actions (seven additive ARM calls, a closed `ALLOWED`
  tuple; the `/v1/action` route calls `actions.preview` and `actions.run`);
  `adopt.py` adoption of existing resources (import blocks made from validated inventory rows, "importing N, changes 0", applied as the
  planner; `/v1/plan` with `mode: import` and `/v1/adopt/apply`);
  `Dockerfile`, `cli.tfrc`, `NOTICE`, `requirements.txt`.
- `refresh.py` refresh-only plans and accepting drift into the state (ADR 0072): `plan` (same signature as `runner.plan`, for
  `mode: refresh_only`) and `accept`, both as the planner; `/v1/plan` with `mode: refresh_only` and `/v1/refresh/accept`. A refresh-only digest is its own domain (it never equals an apply's).
- `prices.py` is not in the tree: the image copies `plugins/_ea_azure/helpers/prices.py` and rewrites one import line.
  `executor/secret_env.py`, `credentials.py` and `store.py` are copied in the same way. Do not fork them here.

## Local Contracts

- **A change set is planned whole and applied one step at a time** (ADR 0075). `POST /v1/plan-set` (`runner.plan_set`) plans
  2 to `gate.SET_MAX_STEPS` template steps in order, each judged with what the earlier steps make (`pending`: the group and the
  IDs, built from plans this executor ran, never from the request). The answer carries each step's digest and
  `gate.set_digest`; applying is `/v1/apply`, once per step id, nothing new. A template's `parents` are proved by `judge`:
  made by an earlier step, or a GET with the planner's key (never a `data` block).
- **A resource group is in scope when the guard rails list it, this executor made it, or an earlier step of the same plan-set
  makes it** (ADR 0074). `store.made_groups` reads `cloud_state_version` and the ledger; an error there is a refusal, never an
  empty set. `gate.check_plan` takes it as `draft["groups_ok"]` and nothing else about scope moves.
- **Nothing here removes, stops, restarts, resizes or replaces anything in Azure.** The runner runs `init`, `workspace`,
  `validate`, `plan`, `show`, `apply` and `plan -detailed-exitcode`; a test scans this directory's source for the word
  for the command that removes, and another scans `arm.py` for a delete verb. `gate.check_plan` refuses any plan that
  deletes, replaces or forgets, and no rail changes that.
- **Free code is judged more strictly than a template** (ADR 0070): a create must be one whose Azure ID `gate._arm_ids` can
  work out (a create is a PUT, and a PUT over something that exists replaces it), `azapi_update_resource` is refused, an
  association may link only a subnet the same plan creates, and no planned value may carry `custom_data`, `user_data`,
  `extension`, `extensionProfile` or `run_command`, and no resource may be imported or moved (adoption is inventory-only,
  ADR 0071). Updates may touch only `MAY_CHANGE[type]`. A module block has one `source`
  word and it is the vendored path.
- Loosening any constant in `gate.py` or an entry of `role.json` is a security change: extend `tests/test_gate.py` or
  `tests/test_role.py` first.
- Nothing is fetched at run time: OpenTofu and the two providers are SHA-256 pinned in the Dockerfile and mirrored to
  `/opt/mirror`; `cli.tfrc` has no `direct` block. Changing a version changes its hashes in the same commit
  (`tests/test_image.py` ties the Dockerfile to `runner.AZURERM_VERSION` and `AZAPI_VERSION`).
- Keys never leave: private halves are sealed (AES-256-GCM under `REZZTOR_CLOUD_KEY`), the PKCS#12 is built in memory,
  and no endpoint or log line returns a key, a token, state or a plan. Errors from OpenTofu are scrubbed of the secrets
  in its environment before they are shown.
- The approved artefact is the plan digest (ADR 0062); `apply` plans again, judges again and applies only that digest,
  once per change id (`cloud_state_version.change_id` is unique).

## Verification

- `REZZTOR_TEST_DB_URL=<your own database> pytest -q -p no:cacheprovider cloud_executor` (the executor's tests are not in
  `testpaths`; `test_store.py` needs PostgreSQL and uses the root `database` fixture). `fake_tofu.py` replays recorded
  plan JSON; no test needs Azure, a real credential or `tofu`.
- The image is built by the coordinator, never from a test: read back `tofu version` and the mirror listing, and check
  that `tofu init` with a provider outside the mirror fails (the Dockerfile's last `RUN` does both).
