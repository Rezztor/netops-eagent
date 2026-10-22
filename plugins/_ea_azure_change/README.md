# Azure changes

Lets the agent **draft** an addition or a small change in Azure and lets an **engineer** approve it. Off until an
administrator turns it on, and inert until the cloud executor is deployed (`cloud` profile) and the Azure change role
is assigned (`docs/guides/azure-changes.md`). The read plugin `_ea_azure` is separate and stays read-only.

## What it will never do

It never deletes, stops, deallocates, restarts, resizes, scales in or swaps anything in Azure (ADR 0065). There is no
setting for it and no code path to it, and the Azure role the executor runs as withholds those actions too. When
someone needs one of those, the agent writes a **suggestion** with a runbook for the engineer, who does it outside the
product. A suggestion is a closed record: nothing approves it and nothing can send it to the executor.

## What is built

- **Templates** (`azure_change draft`): reviewed OpenTofu in the cloud executor, typed values, additions only.
- **Change sets** (`azure_change draft_set`): 2 to 12 ordered template steps (a hub network, its gateway subnet, spokes and
  peerings) as one card, one change document, one CR or SR number and one approval. Every step is planned for real; the set
  applies in order and stops at the first step that does not apply, with nothing rolled back; the rest is drafted again with
  `continues`. The agent shows the steps as a table and asks before drafting.
- **Simple actions** (`azure_action`): start a stopped VM, web app, AKS cluster or PostgreSQL server; add instances to a
  scale set or an App Service plan; add or change a tag. Seven, closed.
- **Suggestions** (`azure_change suggest`): the runbook for a removal or a stop, from a waste or Advisor finding.
- **Free code** (`azure_change draft` with `code`): off until an administrator turns on "Allow free code"; always high risk.
- **Adoption** (`azure_change adopt`): imports what exists into the product's state; only a plan that is "importing N,
  changes 0" gets a card; no number and no document, because nothing in Azure changes.
- **Drift** (`azure_change drift`, `drift_accept`): shows what changed in the portal on what the product made and lets
  the engineer accept it as state. Nothing is ever put back.
- The approval, edit, document, register (Changes tab) and settings endpoints, and the gate.

## How a change goes

1. The agent drafts. The executor plans it; the plan is checked against the guard rails and the "nothing is removed" rule.
2. A change document (PDF and Word) is written, and the card shows the plan, the cost and the plan digest.
3. The engineer downloads the document, takes it through the customer's change process, and returns with the **CR or
   SR number**. Approve stays disabled until the number matches the customer's format, and only the engineer who asked
   can approve (the audit carries both names).
   (A change set is the same four steps for all its steps at once: one document, one number, one Approve.)
4. The change applies once, is verified, the inventory is pulled again and a second version of the document records the
   result. A different plan (a different digest) needs a new document and a new approval.

## If the application restarts mid-apply

The executor finishes the job either way. A minute loop reads every change left `applying` and settles it as the apply would
have: status, verify, the as-applied document, the inventory pull. A change set is settled at the step that was running and is
not continued by the loop: if that step did not apply the set is stopped there; if it did, the set stays `applying` and asking
the assistant to apply it again goes on with the next step, under the same approval.

## Who can do what

`tools.network` (power user, administrator) asks and downloads their own documents; `network.approve` approves what the
engineer asked for; `plugins.manage` (administrator) sets the guard rails, the number format and the keys; a standard
user has none of it. See `docs/guides/azure-changes.md`.

The plan digest, not the plan file, is what is approved. See `AGENTS.md` for the contracts, ADRs 0061 to 0066 and 0070 to
0075 for the reasons. Change sets are the same gate over more than one plan.
Tested against a stand-in executor only; the first real run is test session 2.
