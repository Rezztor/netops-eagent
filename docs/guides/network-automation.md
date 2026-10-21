# Network Automation

Rezztor EAgent can read from and change network devices — Cisco IOS switches and
routers, Dell Enterprise SONiC, and read from Fortinet FortiGate and Palo Alto Networks
firewalls — through Ansible.

Two things are true of it at once, and both matter:

- **Reading is free.** Ask the agent about an interface, a VLAN or a device's
  facts and it just answers. Collecting information does not change anything, so
  it does not wait for anybody.
- **Changing is not.** Nothing reaches a device as a change until an engineer has
  read the exact command lines and approved them. Not a summary of the change —
  the commands.

## Where Ansible actually runs

Not in the chat application, and not in the container that runs the agent's own
code. In a container of its own, which is the only workload in the deployment
allowed to reach your management network, and the only place device credentials
exist.

That separation is the design, not an implementation detail. If someone
compromises the chat application they do not get your network, because the chat
application has no route to it and no credentials for it.

## Asking for information

Just ask.

> What's the description on Gi0/12 on core-sw-1?

> Show me the interface errors across the access switches.

The agent picks a **capability** from a reviewed catalogue and runs it. It cannot
invent a command, write a playbook, or name a device address of its own — it
names a capability and a group, and the executor resolves the rest.

One read, `reads/inventory_facts`, is for the inventory rather than for you: it
collects vendor, model, serial and version in check mode and prints them as an
`EAGENT_FACTS <device> <json>` line that Inventory → **Refresh facts** turns into
proposals (see the inventory guide). It covers every platform below, FortiGate and Palo Alto included.

## Asking for a change

Ask in the same way:

> Set the description on Gi0/12 of core-sw-1 to "finance uplink".

What happens next is the part worth understanding.

**1. The agent drafts it and the devices are asked what would happen.** Ansible
runs the change in check mode against the real device, and the module reports the
exact commands it would push and the exact lines that would change. This is why
drafting a change needs the device to be reachable: commands approved without the
device are commands approved against a guess.

**2. You see the commands.** In the chat, as a card with **Approve** and
**Reject**, and in **Network → Changes**. The rollback commands are shown at the
same time, because approving a way in without a way out is half a decision.

**3. You approve it.** The engineer who asked for the change approves it and is
accountable for it; the audit records both who requested and who approved.
Approving is `network.approve`, held by the power (network engineer) and
administrator roles. The agent can never approve.

**4. At apply time the plan is checked again.** If the device moved between
approval and apply — someone else changed something, or a config was reloaded —
the commands that would now run are not the commands anyone read. The change is
marked **superseded** and refused. Draft it again.

### If it goes wrong

Ask the agent to **verify**: it re-checks the devices and tells you whether
anything is still outstanding. Nothing outstanding means the change took.

Ask it to **roll back**: it runs the undo you approved alongside the change. That
needs no new approval, because it was approved at the same moment.

If a change names no rollback entry, the agent will say so and stop. There is no
reviewed way back, and it will not invent one.

## Reading what happened

**Network → Jobs** shows everything the executor has run: what, where, in check
mode or for real, who asked, and how it ended. Open one to see the commands and
the output.

This is a different record from **Activity**. Activity says who authorised
something; Jobs says what the device reported. Neither can be derived from the
other, which is why both exist.

### What you will not see there

Device passwords and community strings are **stripped from the raw output** and
**kept in the commands** — because the whole point of the approval step is that
you can check a value before it is pushed, and `snmp-server community ****`
cannot be checked. Private keys and vault blobs are removed everywhere,
including from commands: nobody reviews a private key character by character.

## FortiGate and Palo Alto firewalls (reads only)

**Written from the vendors' documented output; not run against a device.** The two vendors' own recommendation for
automation is their management API over HTTPS, so that is what the executor uses: the FortiOS REST API (`GET`
on `/api/v2/monitor/...` and `/api/v2/cmdb/...`) for platform `fortios`, and the PAN-OS XML API (`show` commands and
`type=config&action=show`) for platform `panos`. No Ansible collection is involved: the executor makes the calls
itself with `ansible.builtin.uri`, and the playbooks send `GET` only. Nothing can change a device through them: no
`POST`, no commit, no `set`, `edit` or `delete`, no `request`, `debug`, `clear` or `test` command. A test scans
every one of these playbooks for that.

There is no free-form command for these two platforms. You ask for a named read, and the card shows the exact
requests it sends:

| Ask for | Read | FortiGate sends | Palo Alto sends |
|---|---|---|---|
| model, serial, version, hostname, HA state | `reads/device_facts` | `monitor/system/status`, `cmdb/system/ha` (mode and group only), `monitor/system/ha-peer` | `show system info`, `show high-availability state` |
| interfaces: state and addresses | `reads/interfaces` | `monitor/system/interface` | `show interface all` |
| routing table | `reads/routing_table` | `monitor/router/ipv4` | `show routing route` |
| policy summary: names, actions, counts | `reads/firewall_policy_summary` | `cmdb/firewall/policy` (six fields only) | the running security rules |
| VPN tunnel status | `reads/vpn_tunnels` | `monitor/vpn/ipsec` | `show vpn flow` |
| CPU, memory, disk | `reads/system_resources` | `monitor/system/resource/usage` | `show system resources` |
| sessions | `reads/session_summary` | `monitor/system/resource/usage` | `show session info` |
| ports for the Inventory Ports tab | `reads/inventory_ports` | `monitor/system/interface`, `cmdb/system/interface` and `cmdb/system/zone` (named fields), `monitor/system/status` | `show interface all`, `show system info` |

The Ports tab shows link, speed, addresses, VLAN id and the zone (in the description) for these two; there is no PoE and no
neighbour column, because the neighbour listings' fields are not documented anywhere the reads were written from.
Long tables stop at 200 rows and say the total. A FortiGate is read in the VDOM its token is bound to, and a
Palo Alto in every vsys. Inventory → **Refresh facts** works for both, and Inventory's **Back up now** takes the
configuration (`monitor/system/config/backup?scope=global`, and `type=export&category=configuration`), stored
encrypted and shown redacted like any other (FortiOS `ENC` values, pre-shared keys, SNMP communities and PAN-OS `phash`
and key fields are masked).

### Create the read-only API administrator

The executor never gets more than a read-only role, and the token is bound to the executor's address.

**FortiGate** ([Fortinet: REST API](https://docs.fortinet.com/document/fortigate/7.4.0/administration-guide/940602/rest-api)):

1. *System → Administrator Profiles → Create New*: a profile with every permission set to **Read** (or the
   predefined read-only profile, if your FortiOS version has one). A configuration backup needs read access to the
   system configuration; if the backup is refused, that is the permission to check.
2. *System → Administrators → Create New → REST API Admin*: a name such as `eagent-api`, that profile, and **Trusted
   Hosts** set to the executor's address only. FortiOS shows the token once; copy it into the form below. It cannot be
   shown again, only regenerated. (CLI: `config system api-user`, then `execute api-user generate-key <name>`.)
3. FortiOS 6.4 or later is assumed for `Authorization: Bearer`.

**Palo Alto** ([Palo Alto: get your API key](https://docs.paloaltonetworks.com/pan-os/10-0/pan-os-panorama-api/get-started-with-the-pan-os-xml-api/get-your-api-key),
[API authentication and security](https://docs.paloaltonetworks.com/pan-os/11-0/pan-os-panorama-api/about-the-pan-os-xml-api/structure-of-a-pan-os-xml-api-request/api-authentication-and-security)):

1. *Device → Administrators → Add*: a name such as `eagent-api`, administrator type **Role Based** (or Dynamic) with the
   **Superreader** role, or a custom admin role that allows the XML API read-only (Operational Requests, Configuration
   and, for a backup, Export). Limit the management interface's permitted IP addresses to the executor.
2. Generate its key once, from a workstation that can reach the firewall, with `type=keygen` (see the first link), and
   paste the key into the form below. The key lifetime is set under *Device → Setup → Management → Authentication
   Settings*; if you set one, replace the stored key before it expires. The key is sent in the `X-PAN-KEY` header,
   never in the URL, so it does not appear in the firewall's request log.
3. PAN-OS 9.0 or later is assumed for the `X-PAN-KEY` header.

### Store the token and set the platform

Network → Inventory → Credentials: kind **api_token** (the username can stay empty), then name that credential on a
group whose platform is *Fortinet FortiGate* or *Palo Alto PAN-OS*; Inventory's device sync does this for devices with
that platform. The port is 443 unless the host sets `ansible_port`.

### Certificates

The device certificate is **verified**; there is no switch that turns that off and no fingerprint pin. The executor has
no pin mechanism for SSH either (host keys come from a mounted `known_hosts`), and `uri` cannot pin on the same
connection it reads over, so a pin built from a separate fetch would not be safe. What works, and what both vendors
recommend, is a certificate the executor trusts whose subject alternative name matches the address in Inventory. If
yours is self-signed or from a private CA, mount its PEM into the executor container read-only and set the host
variable `eagent_tls_ca` to that path. A failed read then says the certificate was not trusted; a wrong token says
access was denied.

## What the agent cannot do

Worth stating plainly, because it is the reason this is safe to hand to an agent
at all:

- It cannot write a playbook and run it. It names a reviewed entry from a
  catalogue that ships with the product.
- It cannot send a raw device command.
- It cannot supply a device address. It names a group; the executor owns the
  inventory that resolves it.
- It cannot supply a credential. There is no field for one.
- It cannot approve its own change, or anyone's.
- It cannot free-type a value into a config line. Every variable is typed and
  bounded — even a description is a pattern, not free text.

## Getting set up

Ask your administrator. Network automation ships **off**, and turning it on needs
a container started, an inventory entered and credentials stored — see
[Network automation setup](../setup/network-automation.md).
