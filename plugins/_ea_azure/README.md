# Azure

Reads what exists in an Azure tenant, **read-only**, and keeps it as a dated snapshot with a change log. The
Inventory screen (Cloud) and the agent show the stored snapshot, always with the date of the pull it comes from.
It cannot start, stop, change or delete anything in Azure; adding or changing is the separate `_ea_azure_change` plugin, and a fix
that removes or stops something is only ever a suggestion with a runbook (ADR 0065).

**Verified against one real tenant on 2026-10-03** for certificate sign-in, the Reader role and a pull of networking
resources (`docs/plans/2026-10-03-azure-setup-walkthrough.html`). Other resource types are built and tested against a
stand-in only; their paths stay marked `# unverified:` in `helpers/shape.py` and `helpers/queries.py`.

## What it holds

- A Microsoft Entra application the customer creates, with the Reader role on a management group or subscription.
  The credential is a certificate this product generates (default) or a client secret, kept in `usr/.env` and
  never shown again.
- Five tables (`cloud_account`, `cloud_container`, `cloud_resource`, `cloud_relation`, `cloud_change`) holding
  subscriptions, resource groups, resources, the links between them and what changed between pulls.
- Only allow-listed fields of each resource. Settings, keys, connection strings and app settings are never stored.

## Why only three calls

The client can sign in, list subscriptions and run a fixed Resource Graph query. There is no function that takes a
URL or a method, so a bug or a bad prompt has no write path to reach; the write path was never built.

See `AGENTS.md` for the contracts and `docs/guides/azure.md` for the customer's setup steps.

## Waste

`helpers/waste.py` reads the stored inventory for things that cost money for nothing: a disk attached to nothing, a
public address in use by nothing, an App Service plan with no apps, a VM stopped but not deallocated, an old snapshot,
a gateway with no connection, a load balancer with no back end, and a few more (the list is `waste.RULES`, from the
cost research, section 3.1). It is computed on read and never stored. Ask for it on the Inventory screen (the
"Possible waste" tile), from `POST /api/plugins/_ea_azure/azure_waste`, or in chat (`azure_inventory` with
`attention: waste`).

Every finding names the rule, the evidence and the inventory date, and carries a monthly list price when Microsoft's
public price list can give one (730 hours, before data transfer, usage charges and discounts; it is not the
customer's bill). A finding is a pointer to look, not a verdict: "attached to nothing" is not "nobody needs it", and an
idle firewall or gateway cannot be seen from inventory. A rule whose property is not on the allow-list is inert until
it is; after the allow-list grows, run a pull before trusting a rule that reads an empty list. `drifted` lists the
resources the product made (`cloud_managed`) that changed in a pull with no apply of its own to explain it, until an engineer
accepts it through the change plugin (`azure_change drift_accept`); it is empty until the product has made something. `without: tag:<name>` lists the resources
that lack a tag.

Not verified against a real tenant: the rules ran against a stand-in only. Prices come from rows copied from
prices.azure.com; disk prices use the P10 meter name from there, and the E and S disk meter names are a guess that
answers "no price" if wrong.

## Advice and prices

`azure_advise` answers "where should this go and what will it cost" before anything is drafted: a name that
follows the names already in use, a free address block that overlaps no VNet, peering, route or on-prem address,
the tags in use, the hub, region and zones, and the monthly list price. Everything comes from the stored inventory
except the price, which comes from Microsoft's public price list (`prices.azure.com`, no sign-in, cached a week). With
no internet the advice says the price is unknown. Not verified against a real tenant.

## Live reads and log queries

`azure_read` asks Azure a question about **now** for one stored resource (power state, health, metrics, who changed it, a
firewall's rules, the route or security rule that really applies, whether a flow is allowed, the next hop, VPN BGP
state). The questions are a reviewed table, `reads.yaml` (a GET, or a POST that only makes Azure answer: ADR 0067); the
model names a read and a resource, never an address. Each is approved on a card in the chat (`read_approval`), and the
answer is cut to allow-listed fields, masked and not stored. The probes need the Rezztor Diagnostics role in
`docs/guides/azure.md`.

The same tool asks a Log Analytics workspace (ADR 0069): named reads in `reads.yaml` (`fw_network_denied`,
`fw_application_denied`, `heartbeat_last_seen`) or a free-form `kql` that always asks and can be edited on its card. A
separate log app and certificate sign in, only the workspaces and tables in the settings can be queried, identity
columns are dropped, and an answer stops at 200 rows. The troubleshooting skills (`azure-troubleshooting` and its
specialists) say which reads to ask for which symptom. Not verified against a real tenant (test sessions 3 and 4).

## Cost

`azure_cost` and the **Cost** section of the Azure screen show what the tenant pays, from Azure Cost Management, with
the Reader role the plugin already has (no new access). `spend` is actual cost by service, resource group, resource,
tag or day over this month so far, last month or the last three months; `forecast` is this month so far plus Azure's
projection for the rest; `budgets` lists budgets and cost alerts and the subscriptions that have none; `item` is what one
resource cost, and a waste finding then shows "cost last month".

Azure is asked at most once per scope and question in four hours (`cloud_cost_cache`); the rest comes from what it
said, with the time. It honours Azure's throttling and stops at 30 live reads an hour.

Every answer says its period (UTC), that it is actual cost before tax, the currency, that the data can be up to 72 hours
late (and much later on a free trial), that credits are not deducted, and whether all subscriptions are covered. A
forecast says it is a forecast, how many days of history it rests on, and that it is weak under 90. The figures are
Azure's; the only arithmetic is adding rows of the same currency.

Not verified against a real tenant: every Cost Management response shape, the dimension names and the header formats are
the plugin's reading of Microsoft's reference and are marked `# unverified:` in `helpers/cost.py` until test session 5.

## Microsoft's guidance

The `azure-microsoft-guidance` skill gives the agent Microsoft's official Azure guidance (`microsoft/azure-skills`, MIT): service
troubleshooting (App Service, Functions, Container Apps, AKS, VMs, Event Hubs, Service Bus), KQL, storage, VM sizing, quotas,
reliability, AKS and landing-zone design, Entra app registration. It is **vendored text**: `skills/azure-microsoft-guidance/reference/`
holds the Markdown of twelve allow-listed skills at the commit in `reference/VENDOR.json`, with Microsoft's licence. Nothing
is fetched at run time, no script, hook or MCP configuration of Microsoft's is copied, and the product never calls `az` or
Microsoft's assistant tooling. The skill's rules translate the guidance to this product's tools: a lookup is `azure_inventory`,
a live read is `azure_read`, a command that only reads is a step the engineer runs, an add goes through the approval card,
and anything that deletes or stops is a suggestion with a runbook (ADR 0065).

**Update it** (a developer step, once a month, never at run time): `scripts/sync_azure_skills.sh --check` says whether upstream has
moved and whether the review in `reference/VENDOR.json` (`next_review_due`) is overdue. For a new commit,
`scripts/sync_azure_skills.sh <commit-or-tag>` is a **security review**: it imports nothing, prints and saves to
`tmp/azure-skills-review-<commit>.md` every file added, removed and changed with its diff, and flags each new line that tells the agent
what to do or hides it, deletes, stops, forces or changes access, downloads and runs code, touches a credential, hides text or links
outside Microsoft docs and GitHub, and any new file type or new upstream skill. Read every flag, then run it again with
`--accept --reviewer "Your name"` (refused unless that commit was reviewed; your name and the date go into `VENDOR.json`), read
`git diff plugins/_ea_azure/skills/azure-microsoft-guidance`, run
`pytest plugins/_ea_azure/tests/test_azure_microsoft_guidance.py`, and commit. Add `--from <local clone>` to work offline. It ships
with the next image and offline bundle.
