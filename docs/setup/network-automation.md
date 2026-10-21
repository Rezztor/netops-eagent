# Network Automation Setup

Network automation ships **off** on a hand install, and **on** on a machine
installed from the ISO: there the executor runs from the first start, and you
begin at step 5. Turning it on by hand means starting a second container and
giving it an inventory and credentials. This page is the whole of it.

Read [the user guide](../guides/network-automation.md) first if you have not:
what you are turning on is a system in which the agent can read devices freely
and can change nothing without a named engineer approving exact command lines.

## What you are deploying

One extra container. It is the only workload in the deployment allowed to reach
your management network, and the only place device credentials exist. Neither the
application nor the agent's runner has a route to your devices or a credential
for them, and both are tested to stay that way.

It talks to your devices; nothing talks to it except the application, and (if you
turn it on) a read-only page for when the application is down.

## 1. Three secrets

An installer that was asked for the executor (`COMPOSE_PROFILES=ansible sh
install.sh`, and every machine installed from the ISO) writes them into `.env`
beside the compose file. To switch network automation on later, make them by
hand:

```
# The job token: the application uses this to run playbooks.
openssl rand -hex 32

# The administration token: used to edit the inventory and credentials.
# It must NOT be the same value. The executor refuses to serve its
# configuration plane if the two match, because a shared value would let a
# compromised application rewrite the inventory its own playbooks reach.
openssl rand -hex 32

# The credential key: encrypts stored device credentials. 32 bytes, base64.
head -c32 /dev/urandom | base64
```

**Keep a copy of the credential key.** Changing or losing it makes every stored
device credential undecryptable, and the only way back is entering them again.
It lives in the `.env` beside the compose file, which is why that file is part of
a backup and not something to regenerate on an upgrade.

## 2. Start the container

### Docker Compose

The three values are in `.env`:

```
REZZTOR_ANSIBLE_EXECUTOR_TOKEN=...
REZZTOR_ANSIBLE_ADMIN_TOKEN=...
REZZTOR_ANSIBLE_CREDENTIAL_KEY=...
```

The service sits behind a profile, so it does not run unless asked. Ask in the
same file, so that every later `docker compose up` starts it too:

```
COMPOSE_PROFILES=ansible
```

```
docker compose up -d
```

### Which networks the executor may reach

Compose is the only deployment shape; the Helm chart that used to be described
here went with the k3s appliance on 2026-09-18.

The executor is the one container allowed onto the management network, and it is
allowed there because nothing else is. Which networks that means is a decision
about your estate, made on the host: see the `ansible_net` network in
`deploy/customer/docker-compose.yml` and the network guard,
`deploy/customer/rezztor-guard`. Leaving it unstated is the correct default
-- an executor that could reach the estate before anyone said which estate is a
misconfiguration that works.

## 3. Enable the plugin

Nothing to do when the executor was running at the application's first start:
the application then switches the plugin on by itself, once, and never
overrides a choice somebody made afterwards. Otherwise:
**Plugins → Network Automation → enable.** It ships off (`.toggle-0`), and
enabling writes to the persistent volume, so it survives restarts and upgrades.

Two switches, deliberately: the container is a deployment decision and the plugin
is a runtime one. Enabling the plugin without the container gives you clear errors
rather than a silent no-op — **Network → Health** will say the executor did not
answer.

## 4. Point the application at it

**Settings → Network automation.**

- **Executor URL** — `unix:///run/ansible-socket/executor.sock`, the socket the compose file shares between the
  application and the executor (ADR 0051). The application is not on the executor's network, so there is no TCP
  address to use instead, and an address saved here wins over the compose file: an old saved `http://ansible:8500` is now ignored. Usually left blank: a deployment that runs the `ansible` profile already
  sets `REZZTOR_ANSIBLE_EXECUTOR_URL`, and the plugin enables itself from that.
- **Job token** and **administration token** — nothing to paste: the compose
  file gives the application both (ADR 0036), and the screen says each is
  supplied by the deployment. A token typed here is written to `usr/.env`, is
  never returned by any API, and from then on wins over the deployment's value,
  so type one only on an installation that keeps the administration token away
  from the application (`REZZTOR_ANSIBLE_APP_ADMIN_TOKEN=` in `.env`).

Use **Test** to check reachability. A success means the application can reach the
executor and tells you which catalogue it is pinned to. It says nothing about
whether any device is reachable.

## 5. Enter the inventory and credentials

**Network → Devices.**

Add devices by name and address, then group them. **Group names are what the
agent selects with**, so name them for how people talk about the estate —
`core-switches`, `edge-firewalls` — rather than for the vendor.

Then add credentials and attach one to each group.

**Credentials cannot be read back.** Not from this screen, not from the executor,
not for debugging. If you lose a device password, rotate it. What you get instead
is a fingerprint, which tells you whether the value you are holding is the value
that is loaded.

The inventory names a credential; it never contains one. There is no field
anywhere that puts a password into a group's variables, and the executor would
refuse it if there were.

### Connection variables

Groups need to tell Ansible how to talk to the platform. For Cisco IOS:

```
ansible_network_os = cisco.ios.ios
ansible_connection = ansible.netcommon.network_cli
```

For Fortinet FortiGate and Palo Alto PAN-OS (read over HTTPS by the executor, so no
connection to the device is opened; Inventory's sync sets these for you):

```
ansible_network_os = fortinet.fortios.fortios      # or paloaltonetworks.panos.panos
ansible_connection = local
```

For Dell Enterprise SONiC:

```
ansible_network_os = dellemc.enterprise_sonic.sonic
ansible_connection = ansible.netcommon.httpapi
```

## 6. Decide who approves

Approving a change is `network.approve`; asking for one is `tools.network`. The
power (network engineer) and administrator roles hold both, so **the engineer who
requests a change approves it and is accountable for it** (ADR 0039). Standard
users hold neither. The agent can never approve, and the audit records both
`requested_by` and `approved_by`.

`network.view` — reading the job history — is held by power users as well.

## Operating it

### Restarting it

The application does not control the container, and will not. Doing so would mean
giving the chat application a Docker socket, which is root on the host. **Network
→ Health** reports what the executor says about itself and prints the command:

```
docker compose restart ansible
```

`restart: unless-stopped` already covers a crash.

### The break-glass console

A read-only page on the executor itself, for when the application is the thing
that is broken. Off unless `REZZTOR_ANSIBLE_PUBLIC_PORT` is set, and then bound
inside the container only.

Set the port to turn it on, and put TLS in front of it if it leaves the host:
the console authenticates with HTTP Basic and the administration token is the
password.

```
# In .env beside the compose file, then re-up the ansible profile:
REZZTOR_ANSIBLE_PUBLIC_PORT=8600
```

Then open `http://localhost:8600` and give the administration token as the
password (any username). Leave `ansible.publicBind` at `127.0.0.1` unless TLS
sits in front of a non-loopback bind.

On **Docker Compose** there is no equivalent, so reaching it from a browser means
adding a `ports:` entry to `docker-compose.yml` yourself. That is deliberate:
publishing this port puts a page describing your estate on a network, and the
console authenticates with HTTP Basic, so the token travels in the clear on every
request. **Put TLS in front of it, or only publish it on `127.0.0.1`:**

```yaml
  ansible:
    ports:
      - "127.0.0.1:8600:8600"
```

For a quick check without publishing anything, the image has Python but
deliberately has no `curl`:

```
docker compose exec ansible python -c "
import base64, urllib.request
token = 'YOUR_ADMIN_TOKEN'
req = urllib.request.Request('http://127.0.0.1:8600/')
req.add_header('Authorization', 'Basic ' + base64.b64encode(f'x:{token}'.encode()).decode())
print(urllib.request.urlopen(req).read().decode()[:2000])"
```

### A shell in the container

```
docker compose exec ansible ea-ansible list
docker compose exec ansible ea-ansible run reads/interfaces --limit core-sw-1
```

**Read this before giving anyone that access.** A shell in this container is
equivalent to full authority over every device in the inventory. `ea-ansible`
records what it runs, so the console shows it; a shell user who calls
`ansible-playbook` directly leaves no record at all, and nothing can change that.
A job history with no `cli` rows is not evidence that nobody used a shell.

Treat exec access to this container the way you treat enable on a core switch.

## Upgrading

The catalogue is pinned into the image at build time, and a job quoting a
different catalogue commit is refused. After upgrading the executor image,
re-draft any change that was drafted against the old one; the refusal will tell
you so by name.

## What is not built yet

Being explicit, because a gap you know about is manageable and one you discover
is not:

- **Nothing here has run against a real device.** Every catalogue entry is
  written from the collections' documented interfaces and verified to resolve in
  the built image. Treat your first change on each platform as a test.
- **FortiGate and Palo Alto have reads but no change entries**, deliberately: writing a
  change for a platform with no hardware to try it on is a different risk from
  writing a read. The reads (facts, interfaces, routes, policy summary, VPN, resources,
  sessions, ports and the config backup) call the vendor's HTTPS API from the executor,
  GET only (ADR 0058, and the guide); they are written from documented output and have
  not met a device. Cisco FMC has neither reads nor changes.
- **No ServiceDesk integration.** Tying an approval to a change record is on the
  roadmap and is not built; nothing in the product calls a ServiceDesk API.
