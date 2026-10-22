# Azure cloud inventory

Step-by-step guides with screenshots: [Connect Azure, read-only](azure-setup-read-only.md) · [Use the inventory and the chat](azure-using-inventory-and-chat.md) · [Troubleshoot from the chat](azure-troubleshooting.md) · [Test firewall troubleshooting with a small lab](azure-firewall-test-lab.md).

The Azure plugin reads one Azure tenant and keeps a dated picture of it: subscriptions, resource groups and resources,
each resource placed in one of ten categories. It can only read. It is off until an administrator turns it on in the
Plugins screen. Engineers see the result under **Inventory, Cloud** and can ask about it in chat.

Everything on this page is read-only, and a fix the assistant suggests that removes, stops, restarts or shrinks
something is a **suggestion with a runbook** for an engineer to carry out by hand: the product never does it. Adding or
changing something in Azure is a different, separately switched-on plugin with its own application, its own keys, a CR
or SR number and a change document: see [Azure changes](azure-changes.md).

> **Verified against one real tenant on 3 October 2026**, for certificate sign-in, the Reader role, a pull of
> networking resources and chat answers from them. Compute, storage, database and web resources, several
> subscriptions, and the sign-in failure messages other than "no role" have only run against a stand-in Azure.
> The click-by-click record of that tenant is not in this tree; the steps below are the ones to follow.

## What the administrator does in Azure first

This is done once, by someone who administers the Azure tenant. It takes about ten minutes. An *app registration* is an
account for a program instead of a person.

1. **In this product · Settings, Azure.** Press **Create certificate** and download the public file (`.cer`). The
   private half is made inside the product and never leaves it.
2. **Microsoft Entra admin center · App registrations.** Choose **New registration**. Name it, for example, "Rezztor
   NetOps (read only)". Keep **Single tenant**. Press Register.
3. **Same page · Overview.** Copy two values: **Directory (tenant) ID** and **Application (client) ID**. They are
   identifiers, not secrets.
4. **Same app · Certificates & secrets · Certificates.** Choose **Upload certificate** and pick the `.cer` file from
   step 1. No API permission and no admin consent is needed: access to resources comes from the role in the next step.
5. **Azure portal · Management groups (or one subscription) · Access control (IAM).** Choose **Add role assignment**,
   role **Reader**, member: the app from step 2. The assignment must be active and permanent. An "eligible"
   assignment does not work for a program.
6. **In this product · Settings, Azure.** Paste the two IDs and press **Test connection**. It signs in and lists the
   subscriptions it can read. If the list is what you expect, save, and press **Pull now**.

| Where you give the Reader role | What the inventory then covers | New subscriptions |
| --- | --- | --- |
| The top management group ("Tenant root group") | Everything in the tenant | Covered by themselves, because a new subscription starts under the root. Only a Global Administrator who has raised their own access can assign here, and Microsoft asks that assignments at this level be few. |
| A lower management group (**recommended**) | Every subscription under that group | Covered when they are placed under that group. One assignment, and you choose what is in scope. |
| Individual subscriptions | Only those subscriptions | Not covered until someone adds the role again. Fine for a first test with one subscription. |

**Who can do these steps.** Registering an app needs the Application Developer role in Entra, or the default setting
that lets users register apps. Giving the Reader role needs Owner, User Access Administrator or Role Based Access
Control Administrator at that level.

**What your firewall must allow.** Outgoing HTTPS (port 443) from the application container to
`login.microsoftonline.com` and `management.azure.com`. Nothing comes in from Azure. The cost section and the
`azure_cost` tool use the same two hosts (Cost Management and the billing read are under `management.azure.com`). Prices
in advice come from the public `prices.azure.com`, also port 443; without it they read "unknown".

**If you prefer a client secret.** In step 4 choose **New client secret** instead, with an expiry of 12 months or
less, and paste the value once into the secure form. It works the same. Microsoft's own guidance says a certificate is
the better choice and that secrets should not be used in production.

## The settings

Settings, Azure needs `plugins.manage`. The credential is write-only: after saving, the screen shows only the kind, a
thumbprint (the same value Azure lists beside the uploaded certificate) and the expiry date, and the Sources card warns 30 days before it expires. The private key cannot be
downloaded; to replace it, create a new certificate and upload its public file in Azure.

| Setting | What it does | Default |
| --- | --- | --- |
| Azure cloud | Which Microsoft addresses are used. Only public Azure for now. The addresses are fixed in the code. | Azure public |
| Tenant ID, application ID | Which directory and which app registration. | empty |
| Subscriptions | All the account can read, only a list, or all except a list. **Test connection** fills the list. | all |
| Schedule | Off, every day or every week, with the day, the time and the time zone. A pull missed because the server was off runs once when it comes back. | weekly, Saturday, 02:00 |
| Keep removed resources | How long a resource Azure no longer reports stays visible, marked removed. | 90 days |
| Keep change history | How long the added, changed and removed lines of old pulls are kept. | 12 months |

**Test connection** signs in, lists the readable subscriptions and counts the visible resources. It stores nothing.
Each failure has a plain message: wrong tenant, unknown application, certificate not uploaded, secret expired, no
role assigned, Microsoft not reachable.

## A pull

The first pull, the scheduled pull and **Pull now** run the same steps: sign in, list the subscriptions, read the
subscriptions and resource groups, then read every resource of one subscription at a time, together with what Azure
itself says about them (next section). Each subscription is saved completely or not at all. If one cannot be read to the end, it is marked failed, its stored data is left exactly as it
was, nothing of it is marked removed, and the pull is recorded as **partial**. A subscription the account can no longer
read is shown as "not readable since" with its date. If sign-in fails, the pull is recorded as failed and nothing
stored changes. One pull runs at a time, and **Pull now** is refused within 15 minutes of the last one.

The inventory is **observed**: a pull is saved without approval, dated, and replaced by the next one. Each pull records
what was added, changed and removed since the pull before; **History** lists them with the old and new values. There
is one current picture plus the change list, not a full copy per pull.

## What Azure itself says

Beside the resources, each pull reads four more tables Azure keeps, with the same Reader role and the same single query
call. No new access, no new setting.

| Kind | Where it comes from | What is kept | Shown as |
| --- | --- | --- | --- |
| Advisor | `advisorresources`, all five categories | category, impact, risk, Microsoft's problem and solution text, current and target size, the saving as Microsoft states it (a string, in its currency, with the look-back days) | Advisor on the resource page |
| Policy | `policyresources`, non-compliant states only | the assignment, the definition, the effect (audit, deny...), the category | Policy it does not meet |
| Change records | `resourcechanges`, the last 14 days | who changed it (`changedBy`, its type, the client), when, the operation, and each changed path | Changed in Azure, last 14 days |
| Health | `healthresources` (VMs today) | the availability state, the one before, and Azure's reason for a change | Health |

**Same rule as the inventory.** A change record shows the old and new value of a path only when that path is one the
plugin stores (the same allow-list, applied to the path Azure reports). Every other changed path is listed by name with
"the value is not stored", so an app setting or a script changed in Azure never reaches the database. Values that look
like secrets are masked; tag changes use the tag masking. **Who changed it is kept**, because that is the answer to "who
changed this": it is a person's sign-in name or an application ID, as Azure gives it, and with `require_local_model`
on it stays on a local model like everything else here.

**Replaced, not diffed.** These rows are replaced by each pull (a table of their own, `cloud_signal`), so a
recommendation that appears or a health state that moves is not a "changed" resource and does not light the Changed
count. The change records cover 14 days because that is what Azure keeps; a weekly pull sees them all twice over.
A subscription whose Advisor, policy, change or health read fails is failed as a whole, like a failed page: nothing of
it is saved and what was stored stays as it was. Each table is cut at 5,000 rows per subscription (the change records
newest first); a pull that was cut says so in its counts.

**Pace.** A pull now sends five queries per subscription. Resource Graph allows 15 queries every 5 seconds for one user, so the
pull waits when it has sent 14 in the last 5 seconds.

**Where it shows.** The resource page has a section "What Azure says" with the date it was read; the agent gets the same
four kinds in `azure_inventory` for one resource (`azure_says`) and across the estate with `show` (`advisor`, `policy`,
`health`, `azure_changes`). It says they are Microsoft's words and estimates, quotes a saving as the text Azure gave
(Microsoft's estimate at retail rates), and never adds savings up. Before the first pull after this was added, the
resource page and the tool say the findings have not been read yet; after it, "Azure reported nothing" means Azure
did.

### More fields stored for the waste and advice checks

All are identifiers, counts, states, dates or sizes, each one reviewed line in `shape.ALLOW` (keys in `props`, or a
link in the resource's relations). Never stored, by test: the certificate blocks of an application gateway, all of a
gateway's `vpnClientConfiguration` except its address pool, a snapshot's `sourceUri`.

| Type | Stored |
| --- | --- |
| Every type | `zones` |
| Disk | `time_created`, `last_ownership_update` (with `size_gb`, `disk_state` as before) |
| Snapshot | `size_gb`, `time_created`, `incremental`; link `source` to the disk |
| Virtual machine | `license_type` |
| Public IP | links `ip_configuration`, `nat_gateway_ip`, `public_ip_prefix` |
| Load balancer | `backend_ip_configs`, `backend_addresses`, `inbound_nat_rules` (a list is cut at 100 entries) |
| Application gateway | `sku_name`, `sku_tier`, `sku_capacity`, `backend_addresses`, `backend_ip_configs` |
| Firewall | `sku_tier`; links `firewall_policy`, `virtual_hub` |
| Virtual network gateway | `sku_name`, `vpn_client_pool` |
| Connection | links `vnet_gateway` (either end), `local_gateway` |
| Local network gateway | `address_space`, `gateway_ip` |
| Virtual hub | `address_prefix` |
| ExpressRoute circuit | `provider_state`, `circuit_state` |
| DDoS protection plan | links `protects` to each network |
| Private DNS zone | `vnet_links`, `record_sets` |
| Private endpoint | `manual_connection_states`; links `nic`, `target` |
| SQL database | link `elastic_pool` |
| Log Analytics workspace | `retention_days`, `sku`, `daily_quota_gb` |
| Availability set | links `vm` |
| App Service certificate | `expires` |

**Not yet proven on a real tenant.** Every path above and every Advisor, policy, change and health field is written from
Microsoft's documentation (the pages the stand-in's rows cite are in `tests/fixtures/tenant.py`) and is marked
`# unverified:` in the code until the first test session reads a real tenant. That session also settles: that the four
tables answer to the Reader role and the scoping by subscription; that `resourcechanges` carries `changedBy` for a user
and for an application; whether a value of a secret-looking path arrives raw (it is dropped here either way); and that
`healthresources` has rows for more than virtual machines.

## Cost

Under **Inventory, Cloud** the **Cost** section shows what the tenant paid last month and so far this month, by
service, and the agent answers the same questions in chat with `azure_cost`: spend by service, resource group, resource,
tag or day; a forecast; budgets and cost alerts (and the subscriptions that have no budget); and what one resource cost.
A waste finding shows "cost last month" once a cost answer for that resource has been asked for (ask the agent "what did
this one cost last month?", or ask for last month by resource).

**No new access.** The Reader role already reads Cost Management on a subscription. The plugin makes five reads: the
cost query, the forecast, the list of budgets, the list of cost alerts, and the agreement type of the billing account
(only the type is kept: `MicrosoftCustomerAgreement`, `EnterpriseAgreement`, `MicrosoftOnlineServicesProgram` or
`MicrosoftPartnerAgreement`). It cannot create a budget, change anything, or read an invoice.

**How often Azure is asked.** At most once per subscription and question in four hours (Azure refreshes cost about every
four hours and charges every query against a quota of 12 units per 10 seconds, 600 an hour for the tenant, one unit for
each month of data queried). The answer is kept in the database and shown with the time it was fetched. If Azure cannot
be asked, the last answer is shown marked stale. If Azure says "too many requests", the product waits as long as Azure
asked. The product also stops at 30 reads an hour.

**What every cost answer says**, on the screen and in chat:

- the **period**, in UTC dates, and that it is **actual cost before tax**, in the **currency** Azure reported (two
  currencies are never added together);
- that the data can be **up to 72 hours late** (8 to 24 hours on an Enterprise or Microsoft Customer Agreement) and
  **later still on a free trial**, and that the current month is an estimate until the invoice and can change until the
  fifth day after the period ends;
- that **credits are not deducted** (a free trial's credit, for example): these are the costs of the usage, not what was
  charged;
- whether the **scope is complete**: how many of the stored subscriptions it covers, and which are missing and why (a
  subscription not readable at the last pull, an offer Cost Management does not support, a new subscription: allow 48
  hours). A total over part of the estate is never presented as the whole;
- under a partner (CSP) agreement, that these are pay-as-you-go rates and not the partner's price;
- for a **forecast**: that it is a projection and not a charge, how many days of history it rests on, and that it is weak
  under 90 days (a free trial's is). The forecast is a statistical estimate by Azure and can be wrong either way;
- for budgets: that a budget only alerts and never stops spending, and is evaluated about every 24 hours.

The agent quotes the figures as the tool returned them. It does not add, convert or round them. Cost figures are
what the usage cost; the **list prices** that `azure_advise` and the waste list show are Microsoft's public price for a
new resource (730 hours a month, before data transfer and discounts). They are different things and are labelled so.

**Not verified against a real tenant.** The response shapes, the dimension names, the way Azure pages a long answer and
the format of its quota headers are written from Microsoft's documentation and marked `# unverified:` in
`plugins/_ea_azure/helpers/cost.py`. Test session 5 (cost query with Reader, the dimension names, rows per page, a
forecast on a few days of history, budgets and alerts, the billing read on a free trial, expected to be empty) settles
them. Until then the first real answer may be refused with a plain sentence ("Azure answered the cost read with
something this plugin cannot read") rather than a wrong number.

## What the model can see

The agent has five tools. `azure_inventory` reads the stored picture (it needs `inventory.view`) and always says how
old it is. `azure_advise` checks naming, address space and list prices before something is built. `azure_cost` reads
Cost Management (`inventory.view`; the Cost section above). `azure_read` asks one named question about a stored
resource; an engineer approves it unless a standing setting covers it, and a probe needs the Rezztor Diagnostics role
below. `azure_pull` starts a pull (it needs `inventory.manage`), returns numbers only, and needs no approval card.
None can change anything in Azure, and the agent cannot write its own Azure query.

- **Can see:** names, types, regions, sizes and states of resources; address ranges, subnets, routes, security rules,
  peerings, public addresses; what Azure itself reported (Advisor, policy, health, the 14-day change records with who
  made each change); tags, after secret-looking values are masked; tenant, subscription and resource group
  names; when the last pull ran and what it counted.
- **Can never see:** the certificate key, the client secret, or the access token Microsoft returns; anything Azure
  returned that is not on the allow-list (it is dropped in memory and never stored); app settings, connection strings,
  storage keys, database keys; Azure's raw error text.
- **Configured, not effective.** The inventory knows which route table is attached to a subnet and which routes it
  holds. The routes Azure actually applies need the Rezztor Diagnostics role below. Until that role is assigned, the
  agent says "configured" and names the gap.
- With `require_local_model` on, every Azure tool is refused unless every model that could see the answer runs locally.

## Rezztor Diagnostics

Network probes (effective routes, IP flow verify, next hop, connection check, gateway BGP) are not reads. Azure
classifies them as actions, so the built-in Reader role returns 403. They change nothing in Azure. Assign the custom
role below to the existing read application, at a management group or at the subscriptions that application already
reads. Replace the scope before you create it. Do not assign it at the tenant root.

This role is in addition to Reader. `*/read` is the same read surface Reader already has, so a `NotAction` here does
not hide a secret Reader can already open (the VPN shared-key read is the one to prove by hand, outside the product).
The product refuses that call, Run Command, packet capture and app settings by name. This role must not grant them.
Nothing here deletes, stops, deallocates, restarts or resizes. A need to do that is a suggestion for an engineer, not
a role.

Action strings are Microsoft's, not guessed: Network Watcher names from
[Azure RBAC permissions for Network Watcher](https://learn.microsoft.com/en-us/azure/network-watcher/rbac-permissions)
(ip flow verify, next hop, connection troubleshoot, NSG view). Gateway names match the REST path segments
`getBgpPeerStatus`, `getLearnedRoutes`, `getAdvertisedRoutes`, `getEffectiveRoutes` and `getIkeSas`
([get learned routes](https://learn.microsoft.com/en-us/rest/api/network-gateway/virtual-network-gateways/get-learned-routes?view=rest-network-gateway-2025-09-01)).
`securityGroupView` has no `/read` on that page, so it is not listed. `networkConfigurationDiagnostic` has both,
because Microsoft's pages disagree. Packet capture, `troubleshoot` (it writes a blob) and `queryTroubleshootResult`
are left out. Gateway effective routes and IKE security associations are in the role so it does not have to be edited
when those probes are added; the product does not call them until a catalogue entry allow-lists the answer. Not yet
proven by assigning the role on a tenant.

```json
{
  "Name": "Rezztor Diagnostics",
  "IsCustom": true,
  "Description": "Reader's */read plus named network probes that change nothing. No write, delete, packet capture, VPN troubleshoot, or shared key.",
  "Actions": [
    "*/read",
    "Microsoft.Network/networkWatchers/ipFlowVerify/action",
    "Microsoft.Network/networkWatchers/ipFlowVerify/read",
    "Microsoft.Network/networkWatchers/nextHop/action",
    "Microsoft.Network/networkWatchers/nextHop/read",
    "Microsoft.Network/networkWatchers/securityGroupView/action",
    "Microsoft.Network/networkWatchers/networkConfigurationDiagnostic/action",
    "Microsoft.Network/networkWatchers/networkConfigurationDiagnostic/read",
    "Microsoft.Network/networkWatchers/connectivityCheck/action",
    "Microsoft.Network/networkWatchers/connectivityCheck/read",
    "Microsoft.Network/networkInterfaces/effectiveRouteTable/action",
    "Microsoft.Network/networkInterfaces/effectiveNetworkSecurityGroups/action",
    "Microsoft.Network/virtualNetworkGateways/getBgpPeerStatus/action",
    "Microsoft.Network/virtualNetworkGateways/getLearnedRoutes/action",
    "Microsoft.Network/virtualNetworkGateways/getAdvertisedRoutes/action",
    "Microsoft.Network/virtualNetworkGateways/getEffectiveRoutes/action",
    "Microsoft.Network/connections/getIkeSas/action"
  ],
  "NotActions": [],
  "DataActions": [],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/REPLACE_WITH_SUBSCRIPTION_ID"
  ]
}
```

Create it with `az role definition create --role-definition <file>` after the scope is a real subscription or
management group. Then assign it to the read application's service principal, beside Reader, not instead of it.

## Log queries (optional, a second app)

Questions such as "what did the firewall deny" are answered from a Log Analytics workspace. They use **their own app
registration**, not the read application, so a leak of one key never reaches the other.

1. In Microsoft Entra ID, create a second app registration (for example `rezztor-netops-logs`). Copy its Application
   (client) ID. It needs no API permissions and no secret.
2. In Settings, Azure, Log queries: paste that ID, press **Create log certificate**, download the public file (.cer)
   and upload it under the app's Certificates & secrets.
3. On each Log Analytics workspace to be queried, assign the app **Log Analytics Reader** (Access control (IAM)) and
   nothing else. Microsoft can take up to an hour to apply a new role.
4. In Settings, list the workspaces, one per line: a short name and the workspace ID (the workspace's Overview page).
   Only these can be queried.
5. Check **Tables a query may read**. The default is network and platform diagnostics (Azure Firewall, flow analytics,
   `AzureDiagnostics`, `AzureMetrics`, `Heartbeat`, `Perf`). Leave identity tables (`SigninLogs`, `AuditLogs`,
   `SecurityEvent`) and `AzureActivity` out unless the engineers' need is worth the exposure.
6. Firewall questions need the firewall's diagnostic setting to send its **resource-specific** tables (for example
   `AZFWNetworkRule`) to that workspace.

A query is requested in the chat and shown on a card. A free-form query always asks and the engineer can edit it first;
named queries follow the **Live reads** approval setting. Answers never include who or where from (`Claims`,
`CallerIpAddress`, `UserAgent` and similar columns), are cut at 200 rows, and are not stored. Audit lines:
`azure.read.requested`, `azure.read.edited`, `azure.logs.query` (workspace, time range, a hash of the text, rows),
`azure.logs_credential_set`, `azure.logs_credential_cleared`.

## Permissions and audit

`inventory.view` sees Cloud and asks in chat. `inventory.manage` presses Pull now. `plugins.manage` changes the
settings and the credential, and turns the plugin on or off. No new permission was added. Audit lines:
`azure.settings_saved`, `azure.credential_set`, `azure.connection_tested`, `azure.pull_started`,
`azure.pull_finished` (counts only, no resource names). The change plugin has its own lines and its own permissions
use (`tools.network` to ask, `network.approve` to approve what you asked for, `plugins.manage` for its settings); they
are listed in [Azure changes](azure-changes.md).

If the plugin is turned off, the stored picture stays visible with the notice "Azure is off. This is the picture from
the last pull." and the tools are gone. Nothing is deleted.
