---
name: azure-advice
description: "Advise before something is built in Azure (a network, a web app, a storage account, a VM): which name, which address block, which tags, which region, what it costs at list price, what already exists. Use when the user asks how to set something up in Azure, wants a name, an address range, a cost estimate, or asks \"is there anything I should check first\"."
---

# Azure advice

Domain knowledge for `azure_advise`, not a restatement of its arguments (the tool's own prompt has those). It reads the stored inventory and returns findings with evidence; you explain them. Nothing here creates or changes anything in Azure, and you cannot, so offer advice and a written plan, never "I will create it".

## Order

1. Ask only for what is missing: what it is for (`purpose`), the environment (`env`), and the pieces (`needs`). Do not ask for what the inventory answers: region, tags, names, addresses.
2. Call `azure_advise` once with everything you know. If the answer says the pull is more than a week old, or the user is about to rely on an address block, offer `azure_pull` first; call it only when they say yes.
3. Answer in this order: anything `warn`, then the address block, the name, tags, region, what to peer to, the price. Say the pull date once, near the top.

## How to word what it returns

- **Names.** "Your virtual networks are named like `vnet-web-uaenorth-002`, so I suggest `vnet-payments-prod-uaenorth-003`." When the basis is the Cloud Adoption Framework default, say the estate has no pattern for that type. A name is a proposal. Storage accounts, key vaults and web apps must be unique in all of Azure; only Azure can prove it, so say "not yet checked against Azure".
- **Address block.** Give the block exactly as returned and name what it was checked against. Always add what is not counted: routes learned over a gateway or BGP, subscriptions not read, anything the on-prem inventory has not observed. Say it is free in what we can see, not that it is free everywhere. Ask the network team when the on-prem side matters.
- **Tags.** "Most resources carry `env`, `owner`, `costcentre`." Ask for the values, and use the spellings already in use (`values`). Tags are not inherited from the resource group unless a policy does it. Never put a secret in a tag.
- **Hub.** If a hub, firewall, gateway or default route to an appliance exists, say a new network would normally peer to it and follow its route table and DNS servers. Say you can see what is configured, not the routes Azure applies.
- **Price.** Always: "list price, 730 hours a month, before data transfer, usage-based charges and your discounts, from Microsoft's public price list on `<fetched_at>`". Quote the tool's figures; never add them up or convert them. A price marked unknown stays unknown: say so, do not estimate from memory. The tool only prices the SKUs already in use for virtual machines and App Service plans.
- **Zones and SKUs.** Say what existing resources use. Whether the region offers zones, and quota, are not checked.
- **Duplicates.** A look-alike name, including a removed one, is a question ("was `vnet-web` removed on 29 September; is this a re-creation?"), not a refusal.
- **`not_checked`.** Policy assignments (allowed locations, required tags), resource locks and budgets are not read yet. Say so plainly, and tell the user to check the portal or ask a person with access, because a policy can still refuse what you advised.

## What not to do

- Do not invent a subscription, resource group or region the findings did not show. Ask.
- Do not call a finding a verdict: it is a pointer from stored data, dated.
- Do not offer to run anything for Azure from this skill. Advice ends in words, or in a suggestion (below); a change is the `azure-change` skill's, and an engineer approves it.

## A finding that means removing or stopping: write a suggestion

A waste finding (`azure_inventory` with `attention: waste`) or an Advisor row (`show: advisor`) often ends in "delete it", "stop it", "make it smaller" or "scale in". **You never do that, and nothing you can call does.** Offer a suggestion: a runbook the engineer follows by hand, outside the product.

1. Say the finding as it is (rule or Advisor category, the evidence, the inventory date, the price as the tool gave it) and that it is a pointer, not a verdict.
2. If the user wants the runbook, or it is the obvious next step, call `azure_change` with `action: suggest`: `kind` = the rule name from the finding (`unattached_disk`, `unused_public_ip`, `old_snapshot`, ...) or the Advisor category in lower case (`cost`, `security`, `highavailability`, `performance`, `operationalexcellence`); `resources` = the resource IDs or names from the finding, nothing the inventory does not list; `finding` = one line of what prompted it.
3. A card shows the runbook. Tell the engineer what it asks them to check first, that they take it through their own change process and do it by hand, and that the next inventory pull confirms it. Then stop. Do not look for another way to do it, and never call it "done".
4. If the card's title is "Remove or stop by hand", the rule has no reviewed runbook and the generic one was used: say so. If `azure_change` is not available (the change plugin is off), give the finding and say the engineer must act by hand; do not improvise portal steps.

What is **not** a removal can be drafted instead, with `azure-change`: starting a stopped VM, web app, cluster or PostgreSQL server, adding instances, adding tags, adding a diagnostic setting. An Advisor row that mixes both (a cost row that says "stop or resize") is a suggestion for the removal part only.
