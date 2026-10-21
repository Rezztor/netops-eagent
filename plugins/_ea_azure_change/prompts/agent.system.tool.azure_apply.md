### azure_apply

Apply an Azure change an engineer has already approved. It takes a `change_id` and nothing else: what runs is exactly the plan whose digest the engineer approved, so no argument can alter it.

**You cannot approve.** Call this only after the engineer has told you they approved on the card. It is refused unless the change is approved, in date, unchanged, carries a CR or SR number that matches the customer's format, and its change document was issued and downloaded. If it is refused, read the reason, tell the user **as written**, add the steps on the card in this order and no others (1. Download the change document from the card (PDF or Word). 2. Enter the CR or SR number in the field on the card. 3. Press Approve (it stays off until the document is downloaded and the number is in the right format).), and stop: **do not retry, and do not look for another way to make the change.** If the plan changed since approval the executor applies nothing and the change is superseded: draft it again with `azure_change`.

A change applies once. **A change set** (`draft_set`) is applied with the same call: its steps run in order, each planned again and applied only if it is exactly what was approved, and the set **stops at the first step that does not apply** (a changed plan, a failure, a failed verify): later steps never run and nothing is rolled back, because the product never deletes. Tell the user which steps applied and what they made, which step stopped and why, which did not run, that nothing was rolled back and that the back-out steps for what was applied are in the as-applied document (part 9). To go on with the rest, draft it again with `azure_change` `draft_set` and `continues` (a new document and a new approval; the same CR or SR number may be typed again): never by any other means, and never retry the stopped step under the same approval. The executor admits six changes a minute, so a set of many quick steps waits between steps; say so, do not treat it as a failure. If a step is still running when the wait ends, the reply says so: tell the user and call `azure_apply` again later, which settles that step and goes on. After it, the reply says whether it verified, what was created or changed, and that the inventory is being pulled again; a second document records the result. There is no rollback, delete or stop action here and the product never does those: if the user wants the change undone, the back-out steps in the document (part 9) are for the engineer to follow by hand, outside the product.

Args:
- `action`: `apply` (default) or `close` (a finished change)
- `change_id` (required)

If the reply says the change is still applying, tell the user and check later with `azure_change` `status` (for a set, `azure_apply` again goes on with the steps still to run).

~~~json
{
    "thoughts": ["The engineer says they approved change 7f3a with their CR number. Applying it."],
    "headline": "Applying the approved Azure change",
    "tool_name": "azure_apply",
    "tool_args": {"action": "apply", "change_id": "7f3a2b19c4d5e6f708192a3b4c5d6e7f"}
}
~~~
