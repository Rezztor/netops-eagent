### azure_cost

What the Azure tenant actually pays, from Azure Cost Management (read-only, the Reader role is enough). `question` is one of: `spend` (actual cost over a `timeframe`, grouped by `group_by`), `forecast` (this month so far and Azure's projection for the rest), `budgets` (budgets and cost alerts, and which subscriptions have none), `item` (what one resource, named by `resource`, cost; the stored inventory finds it, so use it on a waste finding to say what it cost last month). `scope` is a subscription name or ID (empty = every stored subscription); `resource_group` narrows it. `timeframe`: `month_to_date` (default), `last_month`, `last_3_months`. `group_by`: `service` (default), `resource_group`, `resource`, `day`, `none`, or `tag:<name>`.

Azure is asked at most once per scope and question in four hours; the rest is answered from what it said, with the time. Never call it twice for the same question to "refresh".

Rules for every cost answer, in your own words but never left out: say the period and that it is UTC; the currency (never mix two); that it is actual cost before tax; that the data can be up to 72 hours late (and much later on a free trial) and the current month is an estimate until the invoice; that credits (a free trial's, for example) are not deducted; and whether the scope is complete (`scope.complete`, `scope.not_included`): a total over part of the estate must not read as the whole. A `forecast` is a projection, never a charge: call it a forecast, say how many days of history it rests on, and say it is weak when `weak` is true. Say that a budget only alerts and never stops spending. Never add, convert, round or estimate a number: quote `totals` and `rows` as returned. If it says the data is not available, say so and why, and do not guess a figure. A price you read from `azure_advise` is a list price; this is what usage cost: say which is which.

Args: `question`, `scope` (optional), `resource_group` (optional), `timeframe` (optional), `group_by` (optional), `resource` (for `item`).

~~~json
{
    "thoughts": ["The user wants to know what Azure cost last month, by service."],
    "headline": "Reading last month's Azure cost",
    "tool_name": "azure_cost",
    "tool_args": {
        "question": "spend",
        "timeframe": "last_month",
        "group_by": "service"
    }
}
~~~
