### azure_advise

Before you propose or build anything in Azure, ask what the estate already does. Returns findings, each with its evidence and the date of the pull: the name pattern in use and a suggested name, Azure's name rules, the first free address block (against every network in Azure, peerings, route tables, local gateways, hubs and the on-prem inventory), tags in use, existing hub and DNS, region, zones, SKUs in use with their list price, and look-alike names including removed ones. Read-only, from the stored inventory; it never contacts Azure. The only outside call is Microsoft's public list price, and a price it could not get is marked unknown.

Say the pull date, each finding's level (`warn` first), and what is `not_checked` (policy, locks and budgets are not read yet). Never do the arithmetic yourself: use the block, the name and the prices as returned, and say a price is a list price. A suggested name is a proposal; global uniqueness (storage accounts, web apps) can only be proven by Azure. Call `azure_pull` only when the user asks for fresh data.

Args: `purpose` (a few words, such as `payments web app`), `env` (prod, test, dev), `region` (optional, such as `uaenorth`), `needs` (a list of plain types: `vnet`, `subnet`, `web app`, `app service plan`, `storage account`, `vm`, `key vault`, `sql server`, `sql database`, `aks`, `nsg`, `route table`, `public ip`, `disk`, `log analytics workspace`, `container registry`), `address_size` (optional prefix length such as `24`; a `vnet` need defaults to 24), `subscription` (optional name or ID).

~~~json
{
    "thoughts": ["The user wants a payments web app with its own VNet; check the estate first."],
    "headline": "Checking Azure naming, address space and prices",
    "tool_name": "azure_advise",
    "tool_args": {
        "purpose": "payments web app",
        "env": "prod",
        "region": "uaenorth",
        "needs": ["vnet", "web app", "app service plan"],
        "address_size": "24"
    }
}
~~~
