# Azure change templates

One directory per template: `main.tf` (OpenTofu code, fixed and reviewed) and its entry in `../templates.yaml`.
The model supplies a name and typed values; the executor renders `terraform.tfvars.json` from the validated values.
The code carries no `provider`, `terraform` or `backend` block (the executor adds them), no `lifecycle` and no
`removed` block, and nothing that removes, stops or restarts. Changing any of that is a security change: extend
`../tests/test_templates.py` first.

## Catalogue entry

| Key | Meaning |
| --- | --- |
| `name`, `summary` | the directory name; one line for the agent and the card |
| `risk` | `low` or `high`. `low` only where nothing carries a data path, an exposure or a cost: resource group, vnet, subnet, nsg, nsg_rule (Allow only, source and destination at least a /8), budget, tags |
| `types` | the managed resource types in `main.tf`, exactly (data sources are not listed); the guard rails' default `resource_types` |
| `variables` | what the model fills. `str` always has a `pattern`; `int` has `min`/`max`; `list` has `max` (and `min`) and an `item` of named fields; `bool`; `optional: true` means `main.tf` has a default |
| `workspace_variables` | values the executor writes from the workspace (`resource_group`, `subscription_id`), never from the model; no default in `main.tf` |
| `may_change` | attribute paths an update may touch; empty means create only |
| `parents` | what a step needs to exist, or to be made by an earlier step of the same change set: `{type, name}` with an optional `resource_group` and `unless`. `type` is a top-level key of `adopt.ARM_TYPES` (any case); `name`, `resource_group` and `unless` name variables of the entry (`unless` a bool). A parent whose optional variable is left out, or whose `unless` is true, is skipped. `Entry.parent_ids(clean, subscription, resource_group)` gives the lower-case `(Azure ID, type)` pairs the executor checks. A template with `parents` never reads them with a `data` block |
| `restarts_existing` | the plan policy refuses the template on a target that exists |
| `backout` | text for the engineer, printed in the change document, never run |

## Decisions

- **A template names its parents and never reads them** (`subnet`, `peering`, `route`, `nsg_rule`). A `data` block would make
  the plan depend on whether the parent exists yet, so a step could not be planned before the step that makes its parent.
  The code refers to the parent by name, or by an ID built from `subscription_id`, `resource_group` and the name, and the
  executor proves the parent exists (in Azure) or is made by an earlier step. `linux_vm` and `private_endpoint` still read
  their subnet and network and cannot follow the step that makes them.
- **Reserved subnet names** (`GatewaySubnet`, `AzureFirewallSubnet`, `AzureFirewallManagementSubnet`, `AzureBastionSubnet`,
  `RouteServerSubnet`) are accepted exactly as spelled, in `vnet` and `subnet`, at /27 (GatewaySubnet) or /26 (the others) or
  larger. `subnet` refuses an NSG or route table on them: in `main.tf`, as a `precondition` (it reads two variables, and a
  `validation` that reads another variable is not available in every OpenTofu version this was checked against).
  `vnet`, which has no NSG, checks the size in a `validation`. Neither rule can be run by a test here.
- **`nsg`** makes a group with no rules and no attachment; rules are `nsg_rule`, and only `subnet` attaches it, to the subnet
  it makes. Attaching an NSG or route table to a subnet that exists re-points live traffic and is never done here.

- **No removal by narrowing.** `nsg_rule` creates `Allow` rules only and has no `access` value: a Deny added above an
  allow cuts traffic, which is a stop. `route` has no next hop `None` (a blackhole is a stop). Rules, routes,
  subnets, peering links and DNS links are separate resources, so adding one never rewrites its parent.
- **A web app is not a `diagnostic_setting` target.** The design says a diagnostic setting on a running web app makes
  Azure cycle it. `restarts_existing: true` would refuse every target (every target exists), so the entry is `false`
  and the target pattern (and a `validation` in code) refuses `Microsoft.Web/sites` and its slots: that case is a
  suggestion.
- **Secure defaults:** HTTPS only, TLS 1.2, public network access off (web app, storage account), no shared key, no
  public blobs, no public IP on VMs, SSH key only, no custom data, Trusted Launch, `default_outbound_access_enabled =
  false` on subnets. The Log Analytics workspace keeps public ingestion and query on, because diagnostic settings,
  agents and the product's log app need them.
- **Tags:** `tags` uses `azapi_update_resource`, which merges into what Azure has and makes nothing, so no existing tag
  is dropped. The plan policy's "a create over an inventory ID is refused" rule must not apply to it.
- **No `azurerm_virtual_network` inline `subnet`, no `azurerm_network_security_group` inline `security_rule`, no inline
  route:** a test scans for them.
- **State:** a workspace is one state. A template planned against a state holding another template's resources would
  plan to remove them, which the policy refuses; the executor must key the state so that this cannot happen.

## Fixtures

`../tests/fixtures/templates/<name>.json` is the first apply and `<name>.update.json` is the one change `may_change`
allows. Both are OpenTofu plan JSON (format_version 1.2) written from the plan JSON format and the azurerm 5.8 / azapi
2.13 documentation. They were not recorded from a run: written from documented output, not run against Azure.
Provider arguments the documentation could not settle carry an `# unverified:` comment in `main.tf`; `tofu validate`
and a real plan in test session 2 prove them and the marks come off then.
