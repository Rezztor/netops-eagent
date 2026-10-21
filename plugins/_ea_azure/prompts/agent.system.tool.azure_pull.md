### azure_pull

Start a pull of the Azure inventory now: the same pull as the Pull now button. It takes no arguments and waits up to 90 seconds. It returns counts only (subscriptions read and failed, resources found, added, changed, removed, returned, how long it took), never the inventory itself: follow it with `azure_inventory` to answer the question.

Call it only when the user asks for fresh data. One pull runs at a time, a pull is refused within a few minutes of the last one (the answer says so), and it needs the engineer's inventory-manage permission. It only reads Azure; it cannot change anything there.

~~~json
{
    "thoughts": ["The user asked me to pull the latest Azure inventory."],
    "headline": "Pulling the Azure inventory",
    "tool_name": "azure_pull",
    "tool_args": {}
}
~~~
