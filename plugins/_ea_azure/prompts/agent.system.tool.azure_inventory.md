### azure_inventory

Ask the stored Azure inventory: what exists in the tenant, where, in what state, and how it is linked. Read-only, and it never contacts Azure: it reads the snapshot the last pull stored. You cannot change anything in Azure, and you can see what is configured, not the effective routes or the live state. For those, say so.

Every answer starts with the date of the pull and names any subscription that was not read in it. Say that date in your reply. Call `azure_pull` only when the user asks for fresh data.

Args (all optional strings): `query` (free text over name, address, type, region, ID), `subscription` and `resource_group` (a name or an ID), `category` (`compute`, `web`, `containers`, `networking`, `storage`, `databases`, `identity_security`, `integration`, `monitoring`, `other`), `type` (a plain name such as `app service`, `virtual network`, `subnet`, `route table`, `nsg`, `vm`, `public ip`, `storage account`, `sql database`, `key vault`, `aks`, or an Azure type such as `microsoft.web/sites`), `region`, `state` (`running`, `stopped`, `failed`, `ok`, `unknown`), `tag` (`name` or `name=value`), `without` (`route_table`, `security_group`, `tags`, or `tag:<name>` for resources that lack that tag, such as `tag:owner`), `attention` (`stopped_failed`, `no_tags`, `exposed`, `changed`, `waste`, `drifted`), `changed_since` (a date, or `last_pull`), `include_removed` (`yes`), `count_by` (`subscription`, `resource_group`, `category`, `type`, `region` or `state`: exact counts), `resource` (one name or Azure ID, for the full record: its stored fields, links in both directions, subnets with each route table and its routes, exposure reasons, and its latest changes). An unknown value of `category`, `state`, `without`, `attention`, `count_by` or `type` is refused with the allowed ones.

`attention: waste` answers "what is wasted?": each finding names the rule that fired, the evidence, and where Microsoft's public price list can say a list price per month (`usd_month`), with a total the tool adds up. Say the inventory date, say the prices are list prices (730 hours, before data transfer, usage charges and discounts), and say a finding is a pointer to look, not a verdict. Never add up or convert prices yourself. `attention: drifted` lists the resources the product made that changed in Azure outside it (no apply of its own explains the change), each with its dated change lines; it is empty until the product has made something. Say it is drift to look at, not a fault. Nothing is put back: to record that the engineer accepts it use `azure_change` with `drift_accept`; to undo it write a suggestion.

`show` answers "what does Azure recommend?", "what is non-compliant?", "what is unhealthy?" and "who changed what lately?" from what Azure itself reported at the last pull: `advisor` (Advisor recommendations, highest impact first), `policy` (policy non-compliance), `health` (Resource Health, only what is not Available) or `azure_changes` (Azure's own change records of the last 14 days, with who made each change, when, and the old and new value of the fields the plugin stores; any other changed field is listed by name with its value withheld). Narrow it with `subscription`, `resource_group` or `query`. A `resource` record carries the same four kinds as `azure_says`. These are Microsoft's words and estimates: say they are Azure's, give the date of the pull, quote a saving as the string it is (Microsoft's estimate at retail rates, over its own look-back window) and never add savings up yourself. They are not a verdict and not a change: you cannot act on them from here.

Limits: at most 25 resources in a list, 50 groups in a count, 60 linked items on one resource; when there are more the answer says how many. Narrow the query, or count instead of listing.

~~~json
{
    "thoughts": ["The user wants to know which subnets have no route table."],
    "headline": "Looking up Azure subnets without a route table",
    "tool_name": "azure_inventory",
    "tool_args": {
        "type": "subnet",
        "without": "route_table"
    }
}
~~~
