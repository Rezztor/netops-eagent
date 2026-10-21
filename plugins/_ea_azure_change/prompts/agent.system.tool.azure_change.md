### azure_change

Draft an **addition or a small change** in Azure from a reviewed template, or write a **suggestion** for something that must be removed or stopped. It never contacts Azure and never changes anything: it asks the cloud executor for a plan, writes a change document (PDF and Word) and puts an approval card in front of the engineer.

**You never delete or stop; you suggest.** The product never deletes, stops, deallocates, restarts, resizes, scales in or swaps anything in Azure, and no setting changes that. There is no argument for it. When the user wants something removed or stopped (an unattached disk, an idle VM, a rule), use `suggest`: it writes a runbook for the engineer to follow by hand, outside the product. Do not look for another way to do it.

**You cannot approve.** The card shows three numbered steps, and when you tell the engineer what to do you say exactly these, in this order, and no others: 1. Download the change document from the card (PDF or Word). 2. Enter the CR or SR number in the field on the card. 3. Press Approve (it stays off until the document is downloaded and the number is in the right format). Typing "approved" or a number in the chat approves nothing. **After drafting, stop** and wait: no other tool call. The card itself writes "Approved on the card with <number>." into the chat once the approval is recorded. When you see that, or the user says they approved, call `azure_apply`: it refuses anything that is not approved, so never tell the user their approval did not work without first calling `azure_apply` or `azure_change` with `action: status`.

Args:
- `action`: `draft` (default), `draft_set`, `resume`, `suggest`, `status`, `list`, `cancel`, `drift`, `drift_accept` or `adopt`
- `draft`: `template` (a name from the list; a wrong one returns the list with what each needs), `subscription` (the GUID, from the inventory), `resource_group`, `purpose` (one or two sentences for the document), and the template's own values as further arguments (or one `variables` object). Ask for any value you do not have; never invent one. When the user asks to change a draft that is still on its card (another address, another name), draft again with `replaces` set to that draft's change id: the old card then says it was superseded; without it the old card stays approvable. Before drafting, use `azure_inventory` and `azure_advise` so the engineer hears what already exists and what Azure advises.
- `draft_set`: a **change set**, one request that needs two or more templates or where one thing depends on another (a hub network, its gateway subnet, two spokes and their peerings). `subscription`, `purpose`, `steps` (2 to 12, in the order they apply: each `{template, resource_group, variables}`; never free code, never a removal or a stop), optional `replaces` (an open change of the user's in the same subscription, to redraft). **Before you call it:** read the inventory and advice, then show the engineer a short table in the chat (`#`, what, template, resource group, key values, depends on) and ask with `ask_user`: "Draft these N steps as one change set?" Draft only after a yes; a change to the table is a new table and a new question, never a silent edit. You never reorder, never add a step the table did not show, and never split one request into several cards unless the user asks for it. One resource stays a single change (`draft`). The executor plans every step for real; you get **one card, one change document, one CR or SR number and one Approve** for the whole set, which applies in order. A step cannot read what an earlier step of the same set makes with a `data` lookup; if the refusal says "apply the set that makes X first", tell the engineer and draft the rest as a second set afterwards. The card has no Edit: to change it, draft again with `replaces`.
- `draft_set` with `continues` (the change id of a set that **stopped part-way**, and no `steps`): drafts the steps from the one that stopped to the last as a new set, planned again, with a new document, a new approval and a new CR or SR number (the engineer may type the same number again). It is the only way to go on with a stopped set; a step that now changes nothing is dropped and named in the reply. Nothing that was applied is undone: the product never deletes.
- `draft` with `code` instead of `template` (**free code**, only where an administrator turned on "Allow free code"; otherwise the reply says so, and you draft from a template or write a suggestion): one text of OpenTofu (HCL), 5,000 characters at most, with `subscription`, `resource_group` and `purpose`. It may only add new resources; the static check and the plan policy refuse anything else (provisioners, other providers, remote modules, file reads, code that runs in Azure, a resource that exists, any delete or narrowing). It is always high risk and the engineer reads the whole code on the card. Refused code is not retried in another shape.
- `resume` (`change_id`, a change or a set): plan the same change again and show its card again (use it the next day, or after the card expired from view). If the plan is the same, the card returns; if Azure moved, the old change is superseded and a new one with a new document is made, and the reply says what differs.
- `suggest`: `kind` (a short name such as `unattached_disk`), `resources` (names or IDs from the inventory), `finding` (the waste rule or Advisor row that prompted it). A runbook card is shown; nothing can run it.
- `status`, `cancel`: `change_id`. `list`: your Azure changes.
- `drift`: list the resources the product made that changed in Azure outside the product (from the stored inventory; it never contacts Azure). Optional `subscription`, `resource_group`.
- `drift_accept` (`resource`: a drifted resource's name or ID): the executor reads Azure (a refresh-only plan) and an **acceptance card** is shown: it adds 0, changes 0 and removes 0, needs no CR or SR number, and the engineer presses Accept to record that Azure's current state is what they expect. After it, stop, like any card. Nothing is put back: there is no action that sets Azure to what a template says; if someone should undo the change, write a suggestion. A `draft` in a resource group with unaccepted drift is refused with the list, so offer `drift` and `drift_accept` first.
- `adopt` (`subscription`, `resource_group`, optional `resources`: names or IDs): bring resources that **already exist** in Azure under the product's management. The resources come from the stored inventory, not from you; the executor writes the import code and plans it. A card shows "importing N, changes 0" and the engineer presses Bring under management: **no CR or SR number**, because nothing in Azure changes. If the plan would change anything the code did not match Azure, so it is refused and there is no card; do not retry another way. Resources already managed, or of a type adoption does not take, are left out and the reply says which. After it, stop, like any card. Adoption never removes or stops anything.

A refusal (a failed check, a cost over the ceiling for the set, a plan that would remove something, a value that does not fit the template, a step that cannot be planned) comes back as the reason, naming the step, and **no card**. Tell the user the reason in plain words. **Never retry a refusal another way**: not with a different template that does the same, not by splitting the change, not by drafting free code. If it is a removal, write a suggestion instead.

~~~json
{
    "thoughts": ["The user wants a VNet for the test environment in uaenorth. I have the subscription and group from the inventory."],
    "headline": "Drafting a virtual network",
    "tool_name": "azure_change",
    "tool_args": {
        "action": "draft",
        "template": "vnet",
        "subscription": "00000000-0000-0000-0000-000000000000",
        "resource_group": "rg-test",
        "purpose": "Network for the test environment.",
        "name": "vnet-test",
        "region": "uaenorth",
        "address_space": [{"cidr": "10.20.0.0/16"}],
        "tags": {"env": "test", "owner": "netops", "costcentre": "1234"}
    }
}
~~~

A change set (after the engineer said yes to the table):

~~~json
{
    "thoughts": ["The engineer confirmed the table: a hub network, its gateway subnet and one spoke with its peering."],
    "headline": "Drafting the hub-and-spoke as one change set",
    "tool_name": "azure_change",
    "tool_args": {
        "action": "draft_set",
        "subscription": "00000000-0000-0000-0000-000000000000",
        "purpose": "Hub network with a gateway subnet and one spoke, peered.",
        "steps": [
            {"template": "vnet", "resource_group": "rg-hub", "variables": {"name": "vnet-hub", "region": "uaenorth", "address_space": [{"cidr": "10.0.0.0/16"}], "tags": {"env": "prod", "owner": "netops", "costcentre": "1234"}}},
            {"template": "subnet", "resource_group": "rg-hub", "variables": {"name": "GatewaySubnet", "vnet_name": "vnet-hub", "address_prefixes": ["10.0.0.0/27"]}}
        ]
    }
}
~~~

A removal becomes a suggestion:

~~~json
{
    "thoughts": ["The disk is unattached and costs money, but I never delete. I will write a runbook for the engineer."],
    "headline": "Writing a suggestion for the unattached disk",
    "tool_name": "azure_change",
    "tool_args": {"action": "suggest", "kind": "unattached_disk", "resources": ["disk-old-01"], "finding": "unattached managed disk, 40 days"}
}
~~~
