# Azure Change Plugin DOX

## Purpose

- Own adding to or changing what exists in an Azure tenant from the chat, behind an engineer's approval of the exact
  plan, a CR or SR number, and a change document. It never removes or stops anything in Azure.
- Binding contract: the cloud executor's plan and apply path, and change sets on top of it.
  Decisions: ADRs 0061 to 0066 and 0070 to 0075.

## Ownership

- This plugin owns the change tools (`azure_change`, `azure_apply`, `azure_action`), the approval, edit, document,
  config and list endpoints, the change document, and **its own gate**. Disabling the plugin removes the tools and the
  gate together; the read plugin `_ea_azure` stays read-only and is not touched.
- The change record is not owned here: it is `_ea_ansible/helpers/change_model.ChangeRecord` and `change_store`, with
  `kind` = `azure_change` | `azure_action` | `azure_suggestion` | `azure_read` and the fields `plan_digest`,
  `reference` and `documents`. One ledger, one audit path.
- The cloud executor (`cloud_executor/`, a separate container) owns OpenTofu, the Azure change keys, the guard rails
  and the state. This plugin reaches it only through `helpers/executor_client.py` over the Unix socket.
- Permissions live in `helpers/api_policy.py` (endpoints) and `helpers/tool_permissions.py` (tools).

## Local Contracts

- **Nothing is removed, stopped, restarted, resized or scaled in (ADR 0065).** There is no setting, argument or code path
  for it. A need becomes an `azure_suggestion` record with a runbook; nothing can run a suggestion, and no path leads
  from one to the executor client.
- **The approved artefact is the plan digest (ADR 0062),** made in the executor, never the plan file. A different digest
  at apply is `superseded`.
- **The approval is a row in PostgreSQL, used exactly once (ADR 0077).** The record file is not believed: `change_store`
  lays the `change_approval` row over it on every read. `api/azure_approve.py` writes the approval and the number with
  `change_store.approve`; `azure_apply`, `azure_action`, `sets.run` (each step, in order), `drift.decide` and `adopt.decide`
  use it with `change_store.take`, one statement, and never set `applying` themselves; `give_back` is for a call the
  executor turned away before anything ran. A record file put back after its apply, a second apply and a change with no
  row are refused. The settling loop pushes the owner's scope before it reads.
- **Approval needs a number and a document (ADR 0066).** `reference` must fully match `guardrails.reference_pattern`
  (40 characters at most); an `issued` document for the digest must exist, have been downloaded, and still match its
  SHA-256. The reference is stored on the record and in the audit and is never part of the plan hash.
- **No key in the application (ADR 0063).** Keys B and C live in the executor. The application holds only the job
  token (`dotenv.KEY_CLOUD_JOB_TOKEN`, or `REZZTOR_CLOUD_JOB_TOKEN_FILE`), never an `API_KEY_*` name.
- **The application cannot read the state (ADR 0064).** The state tables belong to the `eagent_cloud` database role;
  the application role has them revoked (migration 0021).
- The `executor_url` and the guard-rail seed are in `default_config.yaml`; the executor's stored guard rails win.
- **Tools and endpoints (phase 2).** `azure_change` (`draft`, `draft_set`, `resume`, `suggest`, `status`, `list`, `cancel`: no
  `delete`) and `azure_apply` (`apply`, `close`: no rollback) are the only tools; `api/azure_approve.py` (requester
  only; hash, then number, then document), `azure_edit.py`, `azure_document.py`, `azure_changes.py` and
  `azure_change_config.py` are the endpoints. The agent prompts say it cannot approve, stops after drafting and never
  deletes or stops.
- **Simple actions (phase 3).** `azure_action` (`draft`, `apply`) is the same record (`kind` `azure_action`), card, document,
  number and gate for one reviewed ARM call: start a VM, web app, AKS cluster or PostgreSQL server that is stopped, add
  instances to a scale set or an App Service plan, or merge tags. The list is `cloud_executor/actions.yaml`, closed in
  `cloud_executor/actions.py` (`ALLOWED`: seven names); the tool only drafts on a live resource of the stored inventory and
  accepts an executor answer only if it is one POST or PATCH on the management host. The card has no Edit button, and
  `azure_edit` refuses the kind. A start, a scale-in, a stop or a delete is never an action: it is a suggestion
  (`azure_change suggest`, runbooks in `runbooks.yaml`), and the advice skill sends waste findings and Advisor savings there.
- **Free code (phase 7, ADR 0070).** `azure_change draft` with `code` is refused while `allow_free_code` is off (default), with
  the sentence that names who can turn it on (`plugins.manage`, Settings, Azure changes). When on, `records.free_code_problem`
  runs the executor's own static check before any plan; the record is `playbook_name` `free`, always `change-high`, its code
  is its `commands` lines (`records.code_of` joins them back exactly: the digest covers that text), the card shows it whole in
  a collapsed `code` block (5,000 characters at most, refused rather than cut) and has no Edit button; `azure_edit` takes
  `code` for it, `azure_apply` sends `records.source_of(record)`, and the gate's `template_or_free_code_allowed` re-reads the
  switch at apply.
- **A change set is one record (ADR 0073, `helpers/sets.py`).** `azure_change draft_set` (`subscription`, `purpose`, `steps`: 2 to
  `SET_MAX_STEPS` = 12 of `{template, resource_group, variables}`, templates only, never free code; optional `replaces`; or
  `continues` and no `steps`) has the executor plan every step in one `POST /v1/plan-set` call and makes **one** `azure_change`
  record (`playbook_name` `set`, `limit` `<subscription>/<group>,<group>...`, `steps`, `commands` one hashed line per step,
  `rollback_commands` each step's back-out last first, `plan_digest` = the set digest from `cloud_executor.gate.set_digest`), so the
  card, the document, the number, the approval (`azure_approve`, unchanged) and the audit are the single change's. The application
  checks every step's answer and the sums again (`sets.check_answer`); unaccepted drift in any step's group is refused before the
  plan. The tier is `tier.decide_set`. `set_steps_match` (after `plan_unchanged`) recomputes the digest, `commands` and `limit` from
  `steps`: a tampered, reordered or shortened list never runs. The agent shows the steps as a table and asks "Draft these N steps as
  one change set?" before drafting (prompt and skill); it never splits one request into cards unless asked.
  - **The apply** (`sets.run`, from `azure_apply`; the gate lets a set be `approved` or `applying`, every condition still running)
    applies the steps in order through the existing `/v1/apply` under `<set id>.<nn>` and each step's own digest; the executor plans
    again and supersedes a step whose digest differs. **The first step that is not `applied` stops the set** (status `failed`,
    `verify_failed`, or `superseded` when step 1 was superseded; later steps `not_run`; no rollback, no retry under the same
    approval). `continues` drafts steps k to n again as a new set with a new document and approval (the same CR number may be typed
    again). A 429 from the executor (six requests a minute) did not start the step: `sets.run` waits `RATE_WAIT_SECONDS`, says so
    in the tool's progress and tries the same step again at most `RATE_TRIES` times, never failing the step for it. A step still
    running at `apply_wait_seconds` leaves the set `applying`; the next `apply` settles it and goes on. One call per set at a time.
  - A set has no Edit (`azure_edit` refuses it); `azure_changes` rows carry `steps: [{n, title, status}]`; the card's `steps` field
    is `{n, title, adds, lines}` (`cards.decision_card`, the console draws it); `azure.change.step` is audited per step with no plan or value.
- **An interrupted apply is settled by the job loop.** An apply waits on the executor's job inside one tool call; if the application
  restarts meanwhile the executor still finishes it. `extensions/python/job_loop/_65_azure_change_settle.py` calls
  `helpers/settling.settle_open` each minute: for every record that is `applying` with a job (oldest first, at most `PER_TICK` = 3
  looked at) it reads the job as the record's owner (`user_scope` pushed explicitly) and settles it as the tool would have
  (`records.settle`, which finishes the record, verifies, writes the as-applied document and starts the inventory pull; an
  `azure_action` record the way its tool does; `sets.settle` for a set). A set is settled at the step that was running and **never
  continued by the loop**: a step that did not apply ends it stopped there; one that applied leaves it `applying` with the next
  steps to run, for the engineer's `azure_apply` (same approval). Quiet: plugin off, executor not configured or silent, or a record
  that raises changes nothing; a change a call in this process is waiting on (`records.in_flight`) is left to that call.
- **The gate** (`extensions/python/tool_execute_before/_95_azure_change_gate.py`) runs `helpers/conditions.CONDITIONS`
  in the build contract's order (plus `set_steps_match` for a set) and fails closed; the tool runs them again. The reference pattern and the document
  files are checked as they are *now*, not as they were at approval.
- **Drift is shown and accepted, never put back (phase 7, ADR 0072).** `azure_change drift` lists, from the stored inventory, the
  resources the product made that changed in Azure outside it; `drift_accept` (`resource`) has the executor plan `-refresh-only` and
  shows an acceptance card (record `azure_change`, playbook `drift_accept`: adds 0, changes 0, removes 0, **no CR or SR number and no
  document** because nothing in Azure changes). Approving (`api/azure_approve.py` hands it to `helpers/drift.decide`) has the executor
  write Azure's current state into the state (`refresh_accept`, as the read identity, only for the approved digest) and sets
  `cloud_managed.accepted_at`. There is no action that sets Azure to what a template says. `records.build_record` refuses a draft,
  resume or edit in a workspace with unaccepted drift; `drift.resource_problem(native_id)` is the one call a simple action's draft
  needs for the same rule. An acceptance record has no commit, reference or document, so `azure_apply` and the gate refuse it.
- **Adoption imports what exists and proves nothing changes (phase 7, ADR 0071).** `azure_change adopt` (`subscription`,
  `resource_group`, optional `resources`) takes the resources from the stored inventory (never from the model), has the executor
  write the import code (`cloud_executor/adopt.py`) and plan it in `import` mode, and shows a card only for a plan that is
  "importing N, changes 0" (record `azure_change`, playbook `adopt`: **no CR or SR number and no document**, because nothing in Azure
  changes). Approving (`api/azure_approve.py` hands it to `helpers/adopt.decide`) has the executor write the imports into the state
  (`adopt_apply`, as the read identity, only for the approved digest) and lists the resources in `cloud_managed`. Resources already
  managed, or of a type outside the executor's reviewed table, are left out. An adoption record has no commit, reference or document,
  so `azure_apply` and the gate refuse it.
- **A suggestion never reaches the executor.** `azure_change suggest` writes a closed `azure_suggestion` record and a
  runbook; the approve endpoint, `azure_apply` and the gate refuse its kind. `tests/test_azure_change_contract.py` scans
  this plugin for any HTTP client outside `executor_client.py` and for any literal that asks for a removal or a stop.
- **The card and the agent say the same three steps (`records.STEPS`):** 1 download the change document, 2 enter the CR or SR
  number, 3 press Approve. The card (`InteractionCard.tsx`) numbers them in place, keeps Approve off with the reason beside it
  until the document was downloaded and the number matches, and asks `azure_changes` with a `change_id` (the requester's own
  record: `status`, `downloaded`, `reference`, `approved_by_name`, `approved_at`) so it survives a reload and shows what became of
  its change instead of live buttons. The tools' STOP text, the prompts and the skill carry `records.STEPS` verbatim (a test
  compares them); a gate or apply refusal reaches the agent through `records.pass_on`, to be told as written with the same steps.
  `azure_change draft` takes `replaces` (an open change of the requester in the same workspace) so a redraft supersedes it and
  the old card goes quiet; without it both stay approvable.
- **A document download counts for approval only when the requester makes it.** A reviewer with `network.approve` may
  download it (`azure_document`) without unblocking the requester's approval.
- **The document is built here, with the standard library only** (`helpers/pdf.py`, `helpers/docx.py`, `helpers/document.py`; no PyMuPDF, ADR 0066):
  PDF and Word copy of `issued` and `applied` versions, and the `runbook` of a suggestion, each written once and never rewritten.
- **Roles.** A standard user has none of this. A power user (`tools.network`, `network.approve`) asks, downloads their own documents and
  approves what they asked for; as a reviewer they may list and download others'. An administrator adds `plugins.manage`: guard rails, the
  number format, keys, the role test (`azure_change_config`). The Changes tab (`azure_changes`, `inventory.view`) answers `enabled` so a
  switched-off plugin with no records draws nothing.
- Off by default (`.toggle-0`); no `always_enabled`.

## Work Guidance

- Add a tool, endpoint or template only as the build contract's phase lists it, and add its permission row first.
- Loosening any gate constant is a security change: extend the accept and refuse table in the gate's tests first.
- No new dependency. No test contacts Microsoft; use a fake executor.

## Verification

- `pytest plugins/_ea_azure_change tests/test_cloud_operations_schema.py -q` (needs PostgreSQL; see the root `conftest.py`).
- `pytest tests/test_api_permission_coverage.py tests/test_tool_permissions.py tests/test_netops_profiles.py -q`

## Child DOX Index

No child DOX files.
