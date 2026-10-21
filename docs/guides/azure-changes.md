# Azure changes

Step-by-step guides with screenshots: [Set up Azure changes](azure-changes-setup.md) · [Ask for, approve and apply a change](azure-approving-a-change.md).

The product can make changes in Azure from the chat: a resource group, a network, a rule on a network security group,
a storage account, a virtual machine, a web app and the rest of the catalogue. It does this through a separate
container, the **cloud executor**, which runs reviewed OpenTofu. This guide says what the administrator sets up, what
the product will always ask for first, and what it will **never** do. For reading Azure (inventory, advice, waste, cost)
see [Azure cloud inventory](azure.md); that part needs none of this.

> **Status.** Built: the change tools and card, the CR or SR number, the change document, change sets, simple actions,
> suggestions, free code (off), adoption and drift. Written and tested against a stand-in for OpenTofu and for Azure, not yet against
> a real tenant. The first real run is test session 2 in `docs/plans/2026-10-03-azure-operations-design.html`; anything
> below that session has not proved is said to be unverified.

## What the product will never do

These are the owner's rules. They are not settings: there is no switch, no argument and no confirmation that changes
them, and the Azure role the product runs as does not allow them either.

| Never | What happens instead |
| --- | --- |
| Delete anything in Azure. Stop, deallocate, restart, resize, scale in, or swap a deployment slot. | The assistant writes a **suggestion** with a runbook (what to check first, the portal steps, how to confirm) for an engineer to carry out. Nothing can run it. |
| Replace a resource (a change that Azure can only make by removing and making it again). | The plan is refused as a whole before anyone is asked to approve it. |
| Narrow or take away: drop a subnet from a network, a rule from a group, a port or an address from a rule, a tag, a setting that was on. | The plan is refused. Adding is allowed; taking away is not. |
| Change Azure without a number. | Every change is approved on the card only with a CR or SR number in your format, which is stored on the change and in the audit. |
| Change Azure without a document. | Every change produces a PDF and a Word copy for management. The engineer must download it before Approve works; a second version records the result. |

A change that needs one of the "never" actions is not a failure of the product. It is the product telling the engineer
to do that step by hand, with the steps written down.

## How a change goes

1. An engineer asks in the chat. The assistant reads the inventory, advises, and drafts. It does not decide.
2. The executor plans the change and the product checks the plan: nothing deleted or replaced, nothing narrowed, only
   the allowed subscriptions, resource groups, regions and resource types, under the cost ceilings. A refused plan
   shows no card.
3. The engineer gets the **change document** (issued) and the card. The card shows what is added, what is changed
   (`~`), the monthly cost at list price, and "Nothing is deleted, stopped or restarted".
4. The card numbers the engineer's three steps, in place, and the agent says the same three: **1** download the change
   document, **2** enter the CR or SR number, **3** press Approve. Approve stays off, with the reason beside it, until
   the document was downloaded and the number matches the pattern you set. The card asks the server for its change's
   state, so after a reload or in another browser it still knows what was downloaded, and once the change is approved,
   rejected, applied, superseded (a newer plan was made, for example by drafting again with `replaces`), cancelled or
   expired it says so, with the number, who and when, instead of showing live buttons.
5. On approve the executor plans again. If the plan is not the one that was approved (Azure changed meanwhile, or the
   guard rails did) it stops: the change is *superseded* and a new document is made. Otherwise it applies once, checks
   that Azure now matches, reads the subscription back into the inventory, and writes the as-applied document.

A draft is valid for `document_valid_days` (14 by default, 1 to 30). The approver is the engineer who asked (ADR 0039).

## A set of changes: a whole build, one number

A hub network with its gateway subnet, two spokes and their peerings is one request, so it is one **change set**: the
assistant shows you the steps first, as a short table in the chat (`#`, what, template, resource group, key values, depends
on), and asks "Draft these N steps as one change set?". It drafts only after you say yes, and it never splits one request into
several cards unless you ask. You then get **one card, one change document, one CR or SR number and one Approve**, and the
steps apply in the order you saw.

- **Two to twelve steps**, each exactly what a single change is: one reviewed template, one resource group, typed values.
  Free code is never a step, and neither is a removal or a stop. One step is a single change. The order is yours; the
  assistant never reorders.
- **Every step is planned for real before you are asked.** A step may refer to something an earlier step of the same set makes
  (a subnet in a network the set creates, a peering of two new networks, a rule on a new group). A template that has to *read*
  its parent (a virtual machine in a subnet, a private endpoint) cannot be planned until the parent exists: the assistant is
  told "apply the set that makes it first", and the rest becomes a second set afterwards.
- **The limits are the set's.** The 250 USD a month ceiling and the 40-resource limit apply to the **sum** of the steps; the
  1,000 USD total counts what earlier changes added; the set is `change-high` if any step's template is not low risk or the sum
  is over the low-risk limits. The card shows the steps collapsed, each with its plan lines, and the whole set's cost.
- **What you approve is the set digest**, made from every step's plan in order. Before each step the product checks that the
  approval, the number, the document and the steps are still what you approved, and the executor plans that step again: a step
  whose plan is not the one approved is *superseded* and nothing of it runs.
- **It stops at the first step that does not apply**, whether the plan changed, the apply failed or the check afterwards did
  not find the intended state. Later steps never run, **nothing is rolled back** (the product never deletes), and a step is
  not tried again under the same approval. The card says "Applied k-1 of n. Stopped at step k." with a chip per step, the
  as-applied document says `AS APPLIED, PARTIAL (k-1 of n)` and lists the back-out of the steps that did apply, last first
  (part 9), and the assistant tells you what applied, what stopped and why, what did not run, and that nothing was rolled back.
- **The rest is a new set.** Ask the assistant to continue: it drafts the steps from the one that stopped as a new set, plans
  them again (a step that now changes nothing is dropped and named), and gives you a new document to download and a new
  approval. The number must be typed again; the same CR or SR number is accepted, because whether one change request covers
  the remainder is your process, not the product's.
- **A set that is slow is not a set that failed.** The executor accepts six requests a minute, so a set of many quick steps
  pauses between steps (15 seconds, up to six tries for one step, said in the chat's progress) and carries on. If it is still
  waiting when the call ends, the set stays as it was and asking the assistant to apply it again goes on with the next step.
  A step still running when the wait ends is settled the same way.
- **If the application restarts in the middle of a set**, the executor finishes the step that was running. Within a minute the
  product reads that step's result and settles the set at that step: if the step did not apply, the set is stopped there and
  reported as such; if it did, the set stays "applying" with the remaining steps not started, and asking the assistant to apply
  it again goes on, under the same approval. The product never continues a set by itself. The same minute loop settles a
  single change that was interrupted: it becomes applied, failed or verify failed with its as-applied document, as if the
  assistant had waited for it.
- **A set has no Edit button.** To change it, ask the assistant to draft it again; the old card then says it was superseded.

## When someone changes it in the portal (drift)

The product knows which resources it made. When a later inventory pull shows one of them changed (or gone) and no change
of the product's own explains it, it is **drifted**: the Azure screen shows a *Drifted* tile, `azure_inventory` takes
`attention: drifted`, and the assistant can list it (`azure_change drift`) with the dated lines of what changed.

- **The product never puts it back.** There is nothing that sets Azure to what a template says. If someone should undo
  it, the assistant writes a suggestion with a runbook, and you do it by hand.
- **You can accept it.** The assistant asks the executor to read Azure (a refresh-only plan, with the read identity) and
  shows a card that adds 0, changes 0 and removes 0. No CR or SR number and no document are needed: nothing in Azure changes.
  Approve writes what Azure looks like now into the product's state and clears the drift up to what you saw. A later change
  is drift again.
- **A new draft in that resource group is refused** until the drift is accepted. After you accept, the template is not
  changed: if it still says the old value, a later draft plans that as a normal change, on its own card, with its own number.

## Simple actions: start, scale out, tags

Seven reviewed single calls, nothing else (`cloud_executor/actions.yaml`): start a stopped virtual machine, web app, AKS
cluster or PostgreSQL flexible server (`vm_start`, `site_start`, `aks_start`, `pg_flexible_start`), add instances to a
scale set or an App Service plan (`vmss_scale_out`, `plan_scale_out`: the new count must be higher, the size never
changes), and add or change a tag (`tags_merge`; a tag is never removed). The assistant uses `azure_action`, on a
resource it found in the inventory; the card shows the one call that will be made. It is a change like any other: a
change document, a CR or SR number, the approval of the engineer who asked, and the same gate. There is no action that
stops, restarts, resizes, scales in or deletes, and a name that is not on the list is refused.

## When the answer is "remove it": a suggestion

Removing, stopping, restarting, resizing, scaling in and tightening a rule are never run. The assistant writes a
**suggestion** (`azure_change suggest`) from a finding in the inventory (an unattached disk, a stopped VM that still
bills, an Advisor saving): a card with the evidence and a **runbook** (PDF) of reviewed steps, the check to make first,
the portal steps and how to confirm. The engineer does it by hand, outside the product, and the next inventory pull
shows whether it happened. Nothing approves or runs a suggestion; no code path leads from it to the executor.

## Free code (off)

By default the assistant uses only the reviewed templates. An administrator can switch on **Allow free code** (Settings,
Azure changes) to let it write OpenTofu itself when no template fits. Free code is always `change-high`, shows the whole
code collapsed on the card, has no Edit button, may only add (the same plan checks apply, and stricter ones: a resource
that cannot be shown to be new, an unlisted type, code that runs a script on a machine or pastes user data are
refused), and is re-checked at apply. Leave it off unless you have someone who reads OpenTofu.

## Adopting what already exists

`azure_change adopt` brings resources made by hand under the product, so a later change can plan against them. It
takes the resources from the stored inventory (never from the model), has the executor write the import code and plan it,
and shows a card **only** for a plan that is "importing N, changes 0". Approve (no CR or SR number and no document,
because nothing in Azure changes) writes the imports into the product's state and lists the resources as managed.
Resources already managed, and types the executor has no reviewed mapping for, are left out and named.

## Who can do what

| Role | Can |
| --- | --- |
| Standard user | Nothing here: no Azure change tools, no card, no document. |
| Power user (the network engineer) | Ask for a change, a simple action, a suggestion, an adoption or a drift acceptance; download the documents of their own changes; approve **only the changes they asked for**, with a CR or SR number. The Changes tab lists their own records; as a reviewer (`network.approve`) they may list and download everyone's. |
| Administrator | All of the above, plus Settings, Azure changes (`plugins.manage`): the guard rails, the CR or SR format, the keys and the role test. |

The assistant never approves, and typing "approved" in the chat approves nothing. A subordinate agent cannot apply. The
Changes list shows each change with the number it was approved under, its plan digest and its documents, and can be
filtered by number. Every step is in the audit (`azure.change.*`, `azure.action.*`, `azure.suggestion.written`,
`azure.guardrails_saved`, `azure.key_made`, `azure.role_checked`); no line carries a key, a token, state, a plan or a
document.

## What the administrator sets up

Done once, by someone who administers the Azure tenant. Two app registrations are involved, and they are different on
purpose: one only reads, one may change, and neither holds the other's key.

| Key | App registration | Used for | Azure role |
| --- | --- | --- | --- |
| A | The read app from [Azure cloud inventory](azure.md) | Inventory pulls and live reads, in the application | Reader (unchanged) |
| B | The **same** read app, with a **second certificate** | The executor's plans (it only reads) | Reader (unchanged) |
| C | A **new** app, "Rezztor NetOps (changes)" | The executor's applies | The custom role below, and nothing else |

1. **Start the executor** (see `INSTALL.md`, "Optional: Azure changes"): `docker compose --profile cloud up -d`.
2. **In this product · Settings, Azure changes** (needs `plugins.manage`). Enter the tenant ID and the read app's
   client ID, and press **Make planner key**. Download the public certificate it shows. The private half is made inside
   the executor, sealed at rest and never shown.
3. **Entra · the read app · Certificates & secrets · Upload certificate**: the file from step 2. The read app now has
   two certificates; key A is untouched.
4. **Entra · App registrations · New registration**: "Rezztor NetOps (changes)", single tenant. Copy its tenant and
   client IDs.
5. **Settings, Azure changes**: enter them and press **Make applier key**. Download that certificate and upload it to
   the new app the same way.
6. **Azure portal · the subscription · Access control (IAM) · Add · Add custom role · Start from JSON**: select the
   file `cloud_executor/role.portal.json`, after replacing the zeros in `assignableScopes` with the subscription ID (one
   entry per subscription the product may change). The portal accepts only its own layout (`properties.roleName`,
   `properties.permissions[0].actions` and so on) and answers "invalid JSON format" to the layout below, which is the one
   the Azure command line takes (`az role definition create --role-definition @role.json`); the two files hold the same
   role. Then **Add role assignment**: that role, member: the changes app. Give it **no other role**.
7. **Settings, Azure changes**: set the guard rails (below) and press **Test**. It signs in as both apps, lists what
   each can read, and checks that the changes app holds exactly this role and no other. **Changes stay off until it
   says so.**

```json
{
  "Name": "Rezztor Cloud Change",
  "IsCustom": true,
  "Description": "Rezztor EAgent cloud executor: create and update Azure resources, start what is stopped, scale out. Cannot delete, stop, deallocate, restart, resize, scale in, swap slots, assign roles or run code. The product enforces the same rules in code; this role is the second wall.",
  "Actions": [
    "*/read",
    "*/write",
    "Microsoft.Network/virtualNetworks/join/action",
    "Microsoft.Network/virtualNetworks/peer/action",
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Network/virtualNetworks/subnets/joinViaServiceEndpoint/action",
    "Microsoft.Network/networkSecurityGroups/join/action",
    "Microsoft.Network/routeTables/join/action",
    "Microsoft.Network/publicIPAddresses/join/action",
    "Microsoft.Network/networkInterfaces/join/action",
    "Microsoft.Network/privateDnsZones/join/action",
    "Microsoft.Network/firewallPolicies/join/action",
    "Microsoft.Web/serverfarms/Join/Action",
    "Microsoft.Compute/virtualMachines/start/action",
    "Microsoft.Web/sites/start/Action",
    "Microsoft.ContainerService/managedClusters/start/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/start/action"
  ],
  "NotActions": [
    "*/delete",
    "Microsoft.Compute/virtualMachineScaleSets/delete/action",
    "Microsoft.Compute/cloudServices/delete/action",
    "Microsoft.Compute/virtualMachines/deletePreservedOSDisk/action",
    "Microsoft.ContainerService/managedClusters/agentPools/deleteMachines/action",
    "Microsoft.KeyVault/locations/deletedVaults/purge/action",
    "Microsoft.KeyVault/locations/deletedManagedHsms/purge/action",
    "Microsoft.OperationalInsights/workspaces/purge/action",
    "Microsoft.Resources/subscriptions/resourceGroups/moveResources/action",
    "Microsoft.Compute/virtualMachines/deallocate/action",
    "Microsoft.Compute/virtualMachines/powerOff/action",
    "Microsoft.Compute/virtualMachines/restart/action",
    "Microsoft.Compute/virtualMachines/redeploy/action",
    "Microsoft.Compute/virtualMachines/reimage/action",
    "Microsoft.Compute/virtualMachines/generalize/action",
    "Microsoft.Compute/virtualMachines/performMaintenance/action",
    "Microsoft.Compute/virtualMachines/simulateEviction/action",
    "Microsoft.Compute/virtualMachines/installPatches/action",
    "Microsoft.Compute/virtualMachines/attachDetachDataDisks/action",
    "Microsoft.Compute/virtualMachines/rollbackOSDisk/action",
    "Microsoft.Compute/virtualMachineScaleSets/deallocate/action",
    "Microsoft.Compute/virtualMachineScaleSets/powerOff/action",
    "Microsoft.Compute/virtualMachineScaleSets/restart/action",
    "Microsoft.Compute/virtualMachineScaleSets/redeploy/action",
    "Microsoft.Compute/virtualMachineScaleSets/reimage/action",
    "Microsoft.Compute/virtualMachineScaleSets/reimageAll/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/deallocate/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/powerOff/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/restart/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/redeploy/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/reimage/action",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/reimageAll/action",
    "Microsoft.Web/sites/stop/Action",
    "Microsoft.Web/sites/restart/Action",
    "Microsoft.Web/sites/slotsswap/Action",
    "Microsoft.Web/sites/applySlotConfig/Action",
    "Microsoft.Web/sites/resetSlotConfig/Action",
    "Microsoft.Web/sites/slots/stop/Action",
    "Microsoft.Web/sites/slots/restart/Action",
    "Microsoft.Web/sites/slots/slotsswap/Action",
    "Microsoft.Web/serverfarms/restartSites/Action",
    "Microsoft.ContainerService/managedClusters/stop/action",
    "Microsoft.ContainerInstance/containerGroups/stop/action",
    "Microsoft.ContainerInstance/containerGroups/restart/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/stop/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/restart/action",
    "Microsoft.DBforMySQL/flexibleServers/stop/action",
    "Microsoft.DBforMySQL/flexibleServers/restart/action",
    "Microsoft.DBforMySQL/flexibleServers/failover/action",
    "Microsoft.Sql/servers/databases/pause/action",
    "Microsoft.Sql/servers/databases/failover/action",
    "Microsoft.Sql/managedInstances/stop/action",
    "Microsoft.Sql/managedInstances/failover/action",
    "Microsoft.Cache/redis/stop/action",
    "Microsoft.Cache/redis/forceReboot/action",
    "Microsoft.Network/applicationGateways/stop/action",
    "Microsoft.Network/applicationGateways/restart/action",
    "Microsoft.Network/networkVirtualAppliances/restart/action",
    "microsoft.network/virtualnetworkgateways/reset/action",
    "microsoft.network/vpngateways/reset/action",
    "Microsoft.Storage/storageAccounts/failover/action",
    "Microsoft.Compute/virtualMachineScaleSets/scale/action",
    "Microsoft.Insights/AutoscaleSettings/Write",
    "Microsoft.Insights/AutoscaleSettings/Scaledown/Action",
    "Microsoft.Resources/deployments/write",
    "Microsoft.Resources/deploymentStacks/write",
    "Microsoft.Resources/deploymentScripts/write",
    "Microsoft.Automation/*",
    "Microsoft.Storage/storageAccounts/managementPolicies/write",
    "Microsoft.Compute/virtualMachines/extensions/write",
    "Microsoft.Compute/virtualMachines/runCommands/write",
    "Microsoft.Compute/virtualMachines/runCommand/action",
    "Microsoft.Compute/virtualMachineScaleSets/extensions/write",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommands/write",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommand/action",
    "Microsoft.ContainerService/managedClusters/runCommand/action",
    "Microsoft.ContainerInstance/containerGroups/write",
    "Microsoft.Web/sites/publish/Action",
    "Microsoft.Web/sites/publishxml/Action",
    "microsoft.web/sites/extensions/write",
    "Microsoft.Web/sites/sourcecontrols/Write",
    "Microsoft.Authorization/*/Write",
    "Microsoft.Authorization/*/Delete",
    "Microsoft.Authorization/elevateAccess/Action",
    "Microsoft.Blueprint/blueprintAssignments/write",
    "Microsoft.Compute/galleries/share/action",
    "Microsoft.Purview/consents/write",
    "Microsoft.Resources/deploymentStacks/manageDenySetting/action",
    "Microsoft.Subscription/cancel/action",
    "Microsoft.Subscription/enable/action"
  ],
  "DataActions": [],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/00000000-0000-0000-0000-000000000000"
  ]
}
```

**Why a role at all, when the product refuses these changes itself.** Two walls are better than one. The role grants
`*/read`, `*/write` and a short list of named actions (joining a subnet, peering, starting something that is stopped),
and it withholds every delete and every stop-like action by name. If the product had a bug, Azure would still refuse
the call. The role cannot stop a removal that is made *through a write* (a network sent without one of its subnets, a
lower count): the product's plan check does that, and it is the first wall for those. Do not widen the role to make a
change work. If Azure answers 403 and names an action, tell the product's maintainers; an action is added only if it
is not a delete and not a stop.

**Test** judges the role strictly: more than one role, a wildcard other than `*/read` and `*/write`, any action that
deletes, any action on the withheld list, any action the reviewed role does not list, data actions, or a role that
leaves out something the reviewed role withholds are all reasons it says no.

## The guard rails

Settings, Azure changes (`plugins.manage`). They are stored in the executor and are the limits the plan is checked
against. There is no setting for deleting, because deleting is not allowed.

| Rail | Meaning | Default |
| --- | --- | --- |
| Subscriptions | The only subscriptions a change may touch. Empty: nothing can change. | none |
| Resource groups | Existing groups a change may touch, and the prefix a new group's name must start with. | none, `rg-` |
| Regions | The only regions allowed. Empty: nothing can change. | none |
| Resource types | What may be created or changed. | the catalogue's types |
| VM size, largest | Most vCPUs of one virtual machine. | 8 |
| Cost per change, total | The most a single change (for a change set, the **sum** of its steps) may add per month at list price, and the most all approved changes may add together. An unknown price is refused. | 250 USD, 1,000 USD |
| Low-risk cost limit | Above this a change is `change-high` even if its template is low risk. | 20 USD |
| Largest change | Most resources in one change (for a change set, in all its steps together). | 40 |
| Free code | Whether the assistant may write OpenTofu code itself instead of using a template (always `change-high`). | off |
| Reference pattern | The CR or SR number format. A full, case-insensitive match; at most 40 characters. A pattern that does not compile, is longer than 100 characters, or would accept an empty number is refused when you save. | `(CR|SR)-?[0-9]{4,10}` |
| Document valid for | Days a draft stays valid. | 14 |

The cost figures are Microsoft's public list prices for 730 hours a month, before data transfer, reservations and
your discounts, and the card says so. The monthly total counts what earlier applied changes added, and only grows:
the product never removes anything, so a resource an engineer removes by hand still counts until the figure is
reviewed.

## What the executor holds, and what it does not

- The two private keys, sealed with `REZZTOR_CLOUD_KEY`, and the passphrase that encrypts OpenTofu's state. The
  application holds neither. The state lives in the database as ciphertext, in a table the application's database
  role cannot read.
- OpenTofu 1.13.1 and the `azurerm` 5.8.0 and `azapi` 2.13.0 providers, built into the image and pinned by SHA-256.
  Nothing is downloaded at run time and no other provider can be installed. `NOTICE` in the image lists the licences
  (all MPL-2.0) and the sources.
- Each run happens in a fresh directory in memory that is removed afterwards, with the key passed to OpenTofu as an
  environment variable and never written to disk.
- It is one container with one unprivileged user. Plans and applies share its process space; there is no per-run CPU
  or memory limit beyond the container's.

**Outbound access** is HTTPS to `management.azure.com`, `login.microsoftonline.com` and `prices.azure.com`, plus a
new storage account's own data-plane host when one is made. See `INSTALL.md`.

**Backup.** Keep `REZZTOR_CLOUD_STATE_PASSPHRASE` and `REZZTOR_CLOUD_KEY` from `.env` with your other secrets.
Without the passphrase the state cannot be opened and the product can no longer plan a change to what it made.

## Limits you should know about

- **One state per thing made.** A change made from a template has its own state, named by the subscription, the
  resource group, the template and the values that name what it makes. Changing a value that names the thing makes a
  new thing; it does not rename the old one.
- **A change that would create something that already exists is refused** ("it exists"): adopt it first (above).
  Free code and templates can add to what the product manages; they cannot take over a resource made by hand.
- **The plan is checked by what the plan says.** A resource the executor cannot read the future value of (for example a
  name only Azure chooses) cannot be compared with the inventory, so it is refused where that matters.
- **Unverified until test session 2:** the exact price-list names for a public IP address and a private endpoint
  (those prices read "unknown" and are refused unless you allow unknown prices), the permissions the providers need for
  a storage account, and the behaviour of every template's first apply. Each is marked `# unverified:` where the code
  makes the claim.
