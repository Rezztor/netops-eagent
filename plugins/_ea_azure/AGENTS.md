# Azure Plugin DOX

## Purpose

- Own reading what exists in one Azure tenant, read-only, and keeping it as a dated snapshot with a change log. The
  Inventory screen's Cloud view and the agent read the stored snapshot; nothing here can change Azure.
- Build contract: `docs/plans/2026-10-02-azure-cloud-inventory-build.md`; live reads, logs, advice and cost follow
  `docs/plans/2026-10-03-azure-operations-build.md` (phases 1, 4, 5, 6; ADRs 0059, 0060, 0067, 0069). Changing Azure is not this plugin: it is
  `_ea_azure_change`, and a removal or a stop is only ever a suggestion (ADR 0065).

## Ownership

- `helpers/store.py` owns the `cloud_*` tables (`helpers/schema/0016_cloud_inventory.sql`): the only module that
  reads or writes them, except `cloud_signal` (0019), which `helpers/signals.py` owns: what Azure itself reported
  (Advisor, policy non-compliance, its 14-day change records, Resource Health), replaced by each pull, never diffed. Writes are `account`, `apply_containers`, `apply_subscription`, `prune`; the rest are reads.
- `helpers/client.py` owns the four network calls and the exception hierarchy. `helpers/credential.py` owns the
  stored credential. `helpers/config.py` owns settings resolution.
- `helpers/queries.py` owns every query text; `helpers/shape.py` owns the allow-list of stored fields;
  `helpers/categories.py` owns type to category and label; `helpers/collect.py` owns a pull; `helpers/schedule.py` owns
  when one is due; `helpers/exposure.py` owns the exposure rules (computed on read, never stored). `helpers/waste.py` owns the waste rules (same
  rule; `priced` is its one network use, a region and a SKU to `prices`, and it asks `cost.annotate` for last month's cost), served by `api/azure_waste.py` and by
  `azure_inventory` with `attention: waste`.
- `extensions/python/job_loop/_61_azure_pull.py` owns starting the scheduled pull; it only asks `schedule` whether one is
  due and calls `collect`.
- `helpers/logs.py` owns log queries (guard, named log reads, the log app's sign-in and the call).
- `api/azure_*.py` own the HTTP surface; permissions live in `helpers/api_policy.py`. `tools/azure_inventory.py`, `tools/azure_advise.py`, `tools/azure_cost.py`, `tools/azure_read.py` and
  `tools/azure_pull.py` own the agent's view; permissions live in `helpers/tool_permissions.py`.
- `store.drifted` / the `drifted` attention (ADR 0072) read `cloud_managed` (the application's list of what the change plugin made) and
  `cloud_managed.accepted_at` (0022): a `changed` or `removed` change line on a managed resource that no apply explains (the first
  accepted pull after the apply is the apply's own), until an engineer accepts it. Computed on read from stored rows, never stored per
  resource. Reading it is this plugin's; writing `accepted_at` is the change plugin's approval (`_ea_azure_change/helpers/drift.py`).
- `skills/azure-microsoft-guidance` owns Microsoft's vendored guidance (ADR 0076): `SKILL.md` is ours (index and translation rules),
  `reference/` is Microsoft's Markdown, written only by `scripts/sync_azure_skills.sh` and described by `reference/VENDOR.json`.
- `skills/azure-troubleshooting` and the seven specialist skills own the chat runbooks. A fix that removes or stops
  something is a suggestion, never a tool call. The Diagnostics role those probes need is in `docs/guides/azure.md`.

## Local Contracts

- **Read-only is structural.** `client.py` makes three calls (token, list subscriptions, Resource Graph query) and has
  no function taking a URL, path or method; a test scans its source. A write feature is a new, separately reviewed
  module with its own credential, never a branch here. Every query is a constant from `queries.py`.
- **Azure's own findings use the same call and the same rule.** A pull reads `ADVISOR`, `POLICY`, `CHANGES` and
  `HEALTH` (constants in `queries.py`) beside the resources of each subscription, all before anything of it is written, so
  a failed read of any of them fails the subscription like a failed page. `shape.SIGNALS` is their allow-list; a change
  record keeps the old and new value only for a path `ALLOW` stores, and `changedBy` (who) as Azure gives it. Their
  paths are `# unverified:` until a real tenant has been read.
- **A field is stored only if it is on the allow-list in `shape.py`.** Never stored: anything under `osProfile`,
  `customData`, app settings, connection strings, container environment, extension settings, secrets.
- **The credential is not config.** It lives in `usr/.env` (`KEY_AZURE_CLIENT_SECRET`, `KEY_AZURE_CLIENT_CERT`) and is
  read only by `client.token()`. Never an `API_KEY_*` name, never in `secrets._RUNTIME_CREDENTIAL_KEYS`, never returned
  by an endpoint or tool.
- **Instance-wide, no row-level security.** Transactions use `db.NO_SCOPE`. Every foreign key is `DEFERRABLE INITIALLY
  IMMEDIATE` so the backup restore (tables in name order) can load them; keep that on any new key.
- **A snapshot, not a feed.** `apply_subscription` makes one subscription match one complete read: a subscription that
  was not read is not applied and its rows stay as last seen; a resource is marked removed only inside a read that
  completed. A subscription's first read writes no `added` rows (a baseline is not a change).
- **Filters are bound parameters;** `by`, `without` and `attention` are checked against fixed tuples
  (`store.BY`, `WITHOUT`, `ATTENTION`) and refused with `store.BadFilter` naming the allowed values.
- `apply_containers` is told the subscriptions in scope: a stored subscription not in that list gets `readable = false`.
- Tool results are cloud data: `returns_device_data` in `_ea_ansible/helpers/local_model.py` matches `azure_*`, so
  `require_local_model` gates every `azure_*` tool.
- **List prices are a separate read path** (ADR 0059): `helpers/prices.py` makes one GET to the constant host
  `https://prices.azure.com` with a filter built only from validated region and SKU patterns, caches rows in
  `cloud_price` for seven days, and answers `None` when the host is unreachable. It never signs in.
- **`azure_advise` never contacts the tenant.** `helpers/advise.py` computes naming, a free address block, tags, hub,
  region, zones, SKUs in use and the list price from stored rows only; policy, locks and budgets are reported
  `not_checked` until they are stored.
- **Cost is another separate read path** (ADR 0060): `helpers/cost.py` reuses `client.token()` and the same host, and makes
  five reads: Cost Management `query`, `forecast`, `budgets`, `alerts` under a stored subscription GUID (and an optional
  checked resource group name), and the billing account's agreement type. Verbs and leaves are constants, redirects are
  not followed, a `nextLink` stays on the host, and `tests/test_azure_cost.py` scans the source. Answers are kept four
  hours in `cloud_cost_cache` (0020) as cost rows and figures only (no contact e-mails, no user names), served stale
  with their age when Azure cannot be asked, and a 429 or an empty quota pauses every live read. `cost.ask` is the one
  answer for `tools/azure_cost.py` and `api/azure_cost.py`; the cost wording (period, currency, lateness, credits,
  scope, forecast) is code in `cost.py`, never left to the model. `cost.annotate` puts last month's cost on a waste
  finding from the cache only. Its response shapes are `# unverified:` until test session 5.
- **Vendored guidance is text, never code, never fetched** (ADR 0076). `reference/` holds only `.md` files of the script's
  allow-list plus `LICENSE` and `VENDOR.json` (commit, date, SHA-256 of each file); no script, hook, JSON config, image or
  executable, and no file named `SKILL.md` (the script renames them `OVERVIEW.md`, so the loader registers one skill). Do not edit a
  file under `reference/`: `tests/test_azure_microsoft_guidance.py` fails; change the allow-list and re-sync instead. The
  wrapper's translation rules (our tools, an engineer-run step for a read nothing of ours covers, `azure_change` for an add, a
  suggestion for anything that deletes or stops, never claim a command not run) win over every vendored sentence; loosen none.
  **The monthly update** (the owner's rule: fetch every month, and no import unread): `scripts/sync_azure_skills.sh --check` says
  whether upstream has moved and whether `next_review_due` in `VENDOR.json` has passed. For a new commit,
  `scripts/sync_azure_skills.sh <commit>` writes nothing into the plugin: it prints, and saves to
  `tmp/azure-skills-review-<commit>.md`, every file added, removed and changed (the file has each diff) and a flag per new line
  that tells the agent what to do or hides it, deletes, stops, forces or changes access, downloads and runs code, touches a
  credential, hides text, or links outside Microsoft docs and GitHub, plus new file types and new upstream skills. Read every
  flag, then `scripts/sync_azure_skills.sh <commit> --accept --reviewer "Name"`: refused unless a report exists for that
  commit; it records `reviewed_by`, `reviewed_on` and `next_review_due` (a month on). Then read `git diff`, run the test,
  commit; it ships with the next image and offline bundle. A flag that is a real instruction to the agent is not imported: drop
  the skill from the allow-list instead.
- **Live reads are a reviewed table** (ADR 0067): `reads.yaml` + `helpers/live.py` are the only code that asks Azure
  "now". A GET is `role: reader`, a POST is a `diagnostics` probe that only answers a question; nothing else loads.
  `live.NEVER` refuses run command, shared keys, secrets, packet capture and every stop-like suffix (the loader and
  `tests/test_azure_live.py` both fail on one). The model gives a read name, a stored resource and typed values, never an
  address. `tools/azure_read.py` is `net_read`'s shape (`request` card, `run` once, `_95_azure_read_gate.py` and
  `run_refusal` the same function); `api/azure_read_approve.py` approves with no change reference; `read_approval`
  (`every_time` default, `once_per_chat`, `log_only`) can replace the card only for a requester who holds `network.approve`.
  Answers are cut to `keep`, masked and never stored. Shapes are `# unverified:` until test session 3.
- **Log queries are a third path** (ADR 0069): `helpers/logs.py` is the only code that asks Log Analytics. It signs in as the
  log app (key D, `credential.KEY_AZURE_LOGS_CERT`, a certificate of its own; the read credential never signs a log query)
  and makes one POST to `api.loganalytics.azure.com`. Workspace, tables and longest time range come from the `logs` setting,
  never from the model; `guard` lexes the text and accepts one tabular statement over the allowed tables only (its accept
  and refuse table is `tests/test_azure_logs_guard.py`: extend it before loosening anything). A name alone in brackets,
  `(Table)`, is a query after the words in `_TABULAR` (`join`, `in`, `toscalar`, ...) or after anything that is not a word,
  and a function call's argument after any other word; `partition` is refused. Named log reads are
  `kind: kql` rows in `reads.yaml` (`live.parse` skips them; `logs.load` checks each against the guard). `azure_read` carries
  both: a free-form `kql` always asks and its card can be edited through `api/azure_read_edit.py`. Answers drop identity
  columns, cut URLs' query strings, mask every cell and stop at 200 rows and 64 KB; nothing is stored. Both rules go by
  what a column's name holds (`CallerIpAddress1` from a join is still one) and a value that starts `http(s)://` is cut
  under any name; the guard refuses what would give such a column another name without writing it: `['Claims']`,
  a name in pieces or with a backslash, `column_ifexists`, `pack_all`. Not closed: a function stored in the customer's
  workspace is called like a built-in one, and what it reads cannot be told from the query. Shapes are
  `# unverified:` until test sessions 3 and 4.
- Verified on one real tenant on 2026-10-03 (sign-in, Reader, a networking pull); other resource types are not. The
  scheduled pull never runs before the first pull, which is the administrator's.

## Work Guidance

- A new stored field is one path in `shape.ALLOW`, nothing else. A new table column is a new migration.
- Keep the plugin off until an administrator turns it on (`plugin.yaml` has no `always_enabled`).

## Verification

- `pytest plugins/_ea_azure -q` (needs PostgreSQL; see the root `conftest.py`). Never against a real tenant.
- `pytest tests/test_api_permission_coverage.py tests/test_tool_permissions.py -q`

## Child DOX Index

No child DOX files.
