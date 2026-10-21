# Test firewall troubleshooting with a small lab

This guide builds a small Azure lab with one firewall and one VM, breaks one flow on purpose, and then asks the
assistant to find the cause and propose the fix. It takes about two hours and about 2 USD of credit.

Start with [Connect Azure, read-only](azure-setup-read-only.md). The fix in Part 4 also needs
[Set up Azure changes](azure-changes-setup.md). Background: [Troubleshoot Azure from the chat](azure-troubleshooting.md).

> **Not yet proven.** This lab has not run against a real firewall. The log column names and the sign-in to Log
> Analytics are written from Microsoft's documentation. [What to record](#what-to-record) lists the points the test
> settles. Screenshots are added when the lab is first run.

## What the test proves

- The assistant reads the firewall's own logs and names the rule, or the missing rule, that dropped the traffic.
- It drafts the fix on an approval card, with a change document and a CR or SR number.
- A removal or a tightening comes back as a written suggestion. The product never runs it.

| Part | Who does it | Where | Time |
| --- | --- | --- | --- |
| 1. Build the lab | You, by hand | Azure portal | 45 min, of which 10 to 15 waiting for the firewall |
| 2. Connect the logs | You; the product makes the certificate | Product settings and the portal | 15 min |
| 3. Break a flow and diagnose | You ask, the assistant reads | Chat | 10 min, after up to 30 min for the first logs |
| 4. Fix through the card | The assistant drafts, you approve | Chat | 10 min |
| 5. Remove the lab | You, by hand | Azure portal | 5 min |

## The lab

Everything is in one resource group, `rg-netops-fwtest`, in **UAE North**.

```text
vnet-fwtest 10.80.0.0/16
├── snet-workload                  10.80.1.0/24    vm-fwtest (no public IP), route table rt-fwtest
├── AzureFirewallSubnet            10.80.250.0/26  afw-fwtest (Basic), policy afwp-fwtest
└── AzureFirewallManagementSubnet  10.80.251.0/26

vm-fwtest ── all traffic ──> afw-fwtest ──> Internet   (port 80 allowed, port 443 has no rule)
                                  └── logs ──> law-netops-fwtest ──> read by the product's log app
```

### Cost

| Resource | List price, UAE North | For a 4-hour test |
| --- | --- | --- |
| Azure Firewall Basic | 0.395 USD an hour, plus 0.065 per GB | about 1.60 |
| Two Standard public IPs (firewall and its management) | 0.005 an hour each | 0.04 |
| VM B1s with a small disk | about 0.016 an hour | 0.07 |
| Log Analytics workspace, diagnostic setting | free under 5 GB a month; 0.33 per GB processed | about 0 |
| VNet, subnets, route table, resource group | free | 0 |
| **Total** | | **about 2 USD** |

Prices are from Microsoft's public price list, read 3 October 2026. A firewall forgotten for a full day costs about
9.70 USD. Remove the lab the same day.

## Before you start

1. **Azure portal › Cost Management › Budgets.** Create a budget on the subscription with an alert at `10` USD. It
   warns by e-mail. It does not stop anything.
2. **Azure portal › Subscriptions › Usage + quotas.** Check that UAE North has at least 1 free vCPU in the B family. A
   free trial cannot raise quotas. Microsoft does not say whether a free trial may create an Azure Firewall; if the
   portal refuses in Part 1 step 5, note the message.
3. **This product.** The Azure plugin is on and its read app has the Reader role. For Part 4, the Azure change plugin
   is on and its test passes.

## Part 1: Build the lab in the portal

Use these names exactly. The chat examples below use them. Region for everything: **UAE North**.

1. **Resource groups › Create.** Name `rg-netops-fwtest`. Press **Review + create**.
2. **Virtual networks › Create.** Name `vnet-fwtest`, address space `10.80.0.0/16`. On the Security tab leave
   Bastion, Firewall and DDoS off. Add three subnets:

   | Subnet | Range | Subnet purpose |
   | --- | --- | --- |
   | `snet-workload` | `10.80.1.0/24` | Default |
   | `AzureFirewallSubnet` | `10.80.250.0/26` | Azure Firewall (the name is fixed) |
   | `AzureFirewallManagementSubnet` | `10.80.251.0/26` | Firewall Management (a Basic firewall needs it) |

3. **Log Analytics workspaces › Create.** Name `law-netops-fwtest`, same resource group. When it is created, open it
   and copy the **Workspace ID** from Overview. It is an identifier, not a secret.
4. **Virtual machines › Create › Azure virtual machine.** Name `vm-fwtest`, image Ubuntu Server LTS, size
   `Standard_B1s`, authentication SSH public key (generate a new pair; you will not need to log in). Networking:
   virtual network `vnet-fwtest`, subnet `snet-workload`, **Public IP: None**. Create.
5. **Firewalls › Create.** Name `afw-fwtest`, SKU **Basic**, firewall policy: add new, `afwp-fwtest`. Virtual
   network: use existing, `vnet-fwtest`. Public IP: add new, `pip-afw-fwtest`. Management public IP: add new,
   `pip-afw-mgmt-fwtest`. Create, and wait 10 to 15 minutes. Then open the firewall's Overview and note its
   **Private IP** (usually `10.80.250.4`).
6. **The firewall › Monitoring › Diagnostic settings › Add diagnostic setting.** Name `to-law`. Tick the categories
   **Azure Firewall Network Rule** and **Azure Firewall Application Rule**. Destination: **Send to Log Analytics
   workspace**, `law-netops-fwtest`. Destination table: **Resource specific**. Save.

   This choice matters. "Resource specific" writes the tables the product reads (`AZFWNetworkRule`,
   `AZFWApplicationRule`). The other choice writes one mixed table.
7. **Firewall Policies › afwp-fwtest › Network rules › Add a rule collection.** Name `rc-allow-web`, type Network,
   priority `200`, action **Allow**. One rule: name `allow-http`, source `10.80.1.0/24`, protocol TCP, destination
   ports `80`, destination type IP address, destination `*`. Add.

   Port 443 gets no rule on purpose. That is the fault.
8. **Route tables › Create.** Name `rt-fwtest`. Under Routes, add a route: name `default-via-fw`, destination IP
   addresses `0.0.0.0/0`, next hop type **Virtual appliance**, next hop address the firewall's private IP. Under
   Subnets, associate `vnet-fwtest` / `snet-workload`.
9. **Virtual machines › vm-fwtest › Operations › Run command › RunShellScript.** Paste and run. It tries port 80 and
   port 443 twenty times:

   ```bash
   for i in $(seq 1 20); do
     curl -s -m 5 -o /dev/null -w "80  -> %{http_code}\n" http://example.com
     curl -s -m 5 -o /dev/null -w "443 -> %{http_code}\n" https://example.com
     sleep 3
   done
   ```

   Expected: `80 -> 200` and `443 -> 000` (no answer). Logs can take up to 30 minutes to appear the first time.

## Part 2: Connect the logs to the product

Logs are read by their own identity, apart from the inventory one, so the inventory app never holds log access.

1. **This product › Settings › Azure › Logs.** Under **Log certificate** press **Create**, then download the public
   file (`.cer`). The private key stays in the product.
2. **Azure portal › App registrations › New registration.** Name `Rezztor NetOps (logs)`, single tenant, Register.
   Copy its Application (client) ID. Then open Certificates & secrets, the Certificates tab, and upload the `.cer`
   from step 1.
3. **Log Analytics workspaces › law-netops-fwtest › Access control (IAM).** Add role assignment: role **Log Analytics
   Reader**, member `Rezztor NetOps (logs)`, Review + assign. Assign it on this workspace only, not on the
   subscription.
4. **This product › Settings › Azure › Logs.** Paste the ID into **Log application (client) ID**. In **Workspaces**
   add one line, a short name then the workspace ID:

   ```text
   fwtest 00000000-0000-0000-0000-000000000000
   ```

   Leave **Tables a query may read** as it is; the firewall tables are in the default. Save.
5. **This product › Inventory › Cloud.** Press **Pull now**, so the firewall, its policy, the route table and the VM
   are in the stored inventory.

## Part 3: Ask the chat to investigate

Use the **NetOps Operator** or **Network Troubleshooter** agent. Ask in your own words. These work:

| You ask | What should happen |
| --- | --- |
| *vm-fwtest (10.80.1.4) cannot reach the internet on 443 but 80 works. Find out why.* | It reads the inventory: the route sends `snet-workload` to the firewall, and the policy has one rule, for port 80. It asks to read the firewall log. A read card appears. Approve it. |
| (after you approve the read) | It shows the denied lines: source 10.80.1.x, destination port 443, and the reason "no rule matched" (Azure calls it Default Action). It names the rule collection that exists and says none allows 443. |
| *What do you recommend?* | An allow rule for TCP 443 from 10.80.1.0/24, with the choice of destination explained (any, or named sites if you want it tighter). |
| *Show me what the firewall allowed and denied for 10.80.1.4 in the last hour.* | A free-form log query. It always asks first, and the card shows the exact query. |

If the assistant says there are no logs yet, wait, run the VM command again, and ask again after ten minutes.

## Part 4: Fix it through the approval card

1. **This product › Settings › Azure changes › Guard rails.** Add `rg-netops-fwtest` to the existing resource groups
   the product may change, and save. You built this group by hand, so the product does not treat it as its own.
2. **Chat.** Ask: *Add a firewall rule in policy afwp-fwtest allowing TCP 443 from 10.80.1.0/24 to any destination.*
   The assistant drafts it from the firewall rule template. It is marked high risk.
3. **The card.** Step 1: download the change document. Step 2: enter the CR or SR number. Step 3: press **Approve**.
   Then tell the assistant to apply. See [Approve an Azure change](azure-approving-a-change.md).
4. **Azure portal › vm-fwtest › Run command.** Run the same script again. Expected now: `443 -> 200`. Ask the chat to
   confirm from the logs.
5. **Chat, the request the product will not run.** Ask: *Remove the port 80 rule.* Expected: no card that executes.
   You get a suggestion with a runbook, because removing or narrowing a rule cuts traffic for someone.

## What to record

Only a real firewall can answer these. A yes or no for each is enough.

| Check | Why it matters |
| --- | --- |
| Did a free trial let you create the firewall? | Microsoft does not say. |
| Did the Basic firewall write to `AZFWNetworkRule`? | Not confirmed for Basic. If not, the logs are in the older mixed table and a read for it must be added. |
| Did the log app sign in and read? | First real sign-in to Log Analytics. |
| Did the denied lines show, with the reason? | The query filters on the word "Deny"; the exact word is unproven. |
| How long until the first log line? | For this guide. |
| Did the rule change apply and verify? | First real firewall change through the card. |
| Was "remove the rule" a suggestion only? | The product never deletes or narrows. |

## Part 5: Remove the lab the same day

1. **Azure portal › Resource groups › rg-netops-fwtest › Delete resource group.** Type the name to confirm. It
   removes the firewall, the public IPs, the VM, the network, the route table and the workspace. The product never
   deletes, so this is yours to do.
2. **This product.** Pull the inventory again; the resources show as removed. Take `rg-netops-fwtest` out of the
   guard rails. The log app and its workspace line can stay or go.

## If something does not work

| What you see | Likely cause | What to do |
| --- | --- | --- |
| The assistant finds no log lines after 30 minutes | The diagnostic setting writes the mixed table, or no traffic went through | Check "Resource specific" in Part 1 step 6. Check the route table is associated with `snet-workload`. Run the VM script again. |
| "That table is not one a query may read" | The table is not in the settings list | Add it under Settings › Azure › Logs › Tables a query may read. |
| The log read says sign-in failed | The certificate was not uploaded to the logs app, or the application ID is wrong | Compare the thumbprint in Settings with the one in the portal. |
| The log read says no access | The role is missing or has not spread yet | Part 2 step 3. Wait a few minutes. |
| Port 80 also fails from the VM | The rule collection is not saved, or DNS | Open the policy and check `rc-allow-web`. Try `curl http://93.184.215.14`. |
| The change draft is refused for scope | The resource group is not in the guard rails | Part 4 step 1. |
