# Ansible executor

The only workload permitted to reach the management network, and the only place
`ansible-playbook` runs. The application sends a **name, a commit and validated
variables**; there is no field through which playbook content, a device command
or a credential can arrive.

This is not the runner: the runner executes model-authored code and must not
have a route to the management network (ADR 0030).

## Build

From the repository root, so the catalogue and its playbooks come across:

```
docker build -f executor/Dockerfile -t rezztor/eagent-ansible:latest \
  --build-arg CATALOGUE_COMMIT="$(git rev-parse HEAD)" .
```

The build fails rather than warns if a collection did not install: it reads the
collection list back and resolves a module, because a mis-pathed install still
exits 0.

## API

| | |
|---|---|
| `GET /healthz` | Liveness, the catalogue commit a caller must match, the entry list, the installed collection versions and job counts. No auth, no secrets -- a readiness probe cannot hold a token, so nothing here describes a device or a job. |
| `POST /v1/preflight` | `{hosts: ["lab-sonic", ...]}` (at most 10). Job token. Returns inventory facts and a TCP connect check per host; never a secret; creates no job. |
| `GET /v1/networks` | The appliance's network list (`/etc/rezztor/networks/networks.json`, mounted read-only; `REZZTOR_NETWORKS_FILE` overrides the path), re-read when the file changes. Job token. A missing or broken file answers `{"known": false, "networks": []}`. The pre-check's rows also carry `reason` (`ok`, `no_route_known`, `no_answer_on_link`, `no_answer_via_gateway`, `refused`, `network_down`; empty when no list is known), `network` and `routed`, worked out from the executor's own TCP connect and the list. |
| `POST /jobs` | `{playbook_name, commit, variables, limit, check_mode}`. Bearer token. Returns `202` and a `job_id`. |
| `GET /jobs` | History, newest first. `limit`, `offset`, and exact-match `status`, `source`, `playbook`. Bearer token. Omits per-job detail. |
| `GET /jobs/{id}` | Status, exit code, bounded output, and the structured events the approval view is built from. |

A job may also carry `actor` and `change_id`. The executor stores them and acts
on neither: they are labels for a history view, and keeping them out of the
execution fields is what `test_record_only_fields_never_reach_ansible` checks.

| `POST /v1/backups` · `GET /v1/backups[?host=]` · `GET /v1/backups/{host}/{id}` · `GET /v1/backups/{host}/diff?from=&to=` | Config backups (job token). Run (at most 10 hosts), list, and a **redacted** text or diff. **No route returns raw text**; the store is AES-256-GCM (ADR 0047). Raw export: `ea-ansible backup export <host> <id>`. |
| `GET /inventory` · `PUT /inventory` | The device inventory. **Administration token.** |
| `GET /credentials` · `PUT /credentials/{name}` · `DELETE /credentials/{name}` | Device credentials, write-only. **Administration token.** |

**Two tokens, and neither works where the other belongs.**

The **job token** (`REZZTOR_ANSIBLE_EXECUTOR_TOKEN`) runs catalogue entries. The
agent holds it, so it is the token that travels and the one a compromised
application hands over.

The **administration token** (`REZZTOR_ANSIBLE_ADMIN_TOKEN`) edits the inventory
and the credentials. Nothing in the agent's path has it. That is the point: a
compromised agent can run the reviewed playbooks it was always allowed to run,
and cannot rewrite the inventory that decides which devices those playbooks
reach.

Setting them to the same value is refused rather than allowed. A deployment that
does that has not made a smaller mistake than one that set no administration
token -- it has made a bigger one that looks like it works.

**It fails closed.** With no `REZZTOR_ANSIBLE_EXECUTOR_TOKEN` configured it
accepts nothing at all, rather than accepting everything. With no
`REZZTOR_ANSIBLE_ADMIN_TOKEN`, the configuration plane accepts nothing and jobs
still run.

**One job at a time, but a second caller waits.** Concurrency defaults to one
because network change is not a throughput problem. What changed is what happens
to the second arrival: it queues. An engineer told "try again shortly" twice has
learned the button is unreliable, and the cure is a short queue, not more
concurrency.

Two refusals survive, because waiting would be the wrong answer to both:

- the queue is already at `REZZTOR_ANSIBLE_MAX_QUEUED_JOBS`, so the wait would be
  meaningless;
- more than `REZZTOR_ANSIBLE_MAX_JOBS_PER_MINUTE` arrived in the last minute,
  which is faster than a person.

The rate limit lives here rather than in the application, because a limit only
the application enforces is not a limit -- the console and the shell reach the
same executor. `/healthz` reports the limits it is applying so a settings screen
can show the number that is actually in force.

A refused job is still recorded, with the refusal as its error. "Why did nothing
run" is a question about the jobs that did not.

## History

Jobs are kept in SQLite at `/var/lib/eagent-ansible/jobs.sqlite`, which is the
one path this container writes to that outlives it. Everything else is tmpfs.

This is a second record, not a duplicate of the application's audit trail. The
audit trail answers *who authorised what*; this answers *what the device said* --
the per-task `commands` and `diff` an engineer reads. Neither is derivable from
the other, which is why both exist.

Retention applies both a window and a row cap, because an idle month must not
keep a job forever and a busy afternoon must not fill the volume.

## What is written down

Everything a job produces passes through `redaction.py` before it is stored, and
the interesting part is what it leaves alone.

Key material -- private keys, vault blobs, bearer tokens -- is removed wherever
it appears, including from command lines. Nobody reviews a private key character
by character, and its presence in a command is the finding rather than its
content.

Device passwords and SNMP community strings are removed from raw stdout and
**kept** in the commands an approver reads. Condition 2 is that an engineer
approves the exact command lines a change would push; `snmp-server community ****`
cannot be checked against what was intended, and an approval that cannot be
checked is a ritual.

Raw stdout gets the full treatment because it is a debugging convenience, not an
approval artifact, and it is where a missing `no_log` ends up.

## The break-glass console

A second, **read-only** door at `REZZTOR_ANSIBLE_PUBLIC_PORT`, for when the
application is the thing that is broken. Health, what has run, and one job's
commands and output. HTTP Basic with the administration token as the password.

Three narrowings, because a published port on the one workload that can reach the
estate is the riskiest part of this design (ADR 0032):

- **Off by default.** No port set, no listener.
- **Loopback unless told otherwise.** `REZZTOR_ANSIBLE_PUBLIC_BIND` is a separate
  decision, and one that needs TLS in front of it — Basic credentials travel in
  the clear on every request.
- **No route changes anything.** There is no `POST` in the console at all.
  Browsers attach cached Basic credentials to cross-origin form posts, so a
  button that ran a catalogue read would have meant any page anywhere could make
  this executor talk to a device. Running a job needs the API and a bearer token,
  which a browser cannot be tricked into sending.

## The shell

`ea-ansible` is on the PATH inside the container, for an engineer who has
`docker exec`ed in:

```
ea-ansible list
ea-ansible show changes/ios_vlan_name
ea-ansible run changes/ios_vlan_name --limit core-sw-1 --check -e vlan_id=40 -e vlan_name=finance
```

It validates variables against the same catalogue code the API uses, refuses to
run without `--limit`, and records what it ran with `source: cli` so the console
shows it beside everything else.

**It is a convenience, not a control.** A shell in this container can call
`ansible-playbook` directly, against any device in the inventory, and nothing
here would know. Shell access to this container is equivalent to full authority
over every device it can reach — that is the nature of the container, not a gap
in the wrapper. A job history with no `cli` rows is not evidence that nobody used
a shell.

## Configuration

| Variable | Default | |
|---|---|---|
| `REZZTOR_ANSIBLE_EXECUTOR_TOKEN` | — | Required. The job token. Without it the API refuses every authenticated request. |
| `REZZTOR_ANSIBLE_ADMIN_TOKEN` | — | The configuration plane. Must differ from the job token. Without it the inventory and credentials cannot be changed. |
| `REZZTOR_ANSIBLE_CREDENTIAL_KEY` | — | 32 bytes, base64. Without it no credential is stored at all. |
| `REZZTOR_ANSIBLE_EXECUTOR_TOKEN_FILE`, `REZZTOR_ANSIBLE_ADMIN_TOKEN_FILE`, `REZZTOR_ANSIBLE_CREDENTIAL_KEY_FILE` | — | The same three secrets read from a mounted file (a Docker secret) instead of the environment. Read at the moment of use; setting both forms of one is refused. |
| `REZZTOR_ANSIBLE_SOCKET` | — | Listen on this Unix socket (mode 0660) and on no TCP port; the application's address is then `unix://<path>` (ADR 0051). Unset, the executor listens on `REZZTOR_ANSIBLE_BIND`:`REZZTOR_ANSIBLE_PORT` (`0.0.0.0:8500`). The compose files set it. |
| `REZZTOR_ANSIBLE_PUBLIC_PORT` | — | Break-glass console. Unset means no listener. |
| `REZZTOR_ANSIBLE_PUBLIC_BIND` | `127.0.0.1` | Where that console binds. Changing it needs TLS in front. |
| `REZZTOR_ANSIBLE_CATALOGUE_COMMIT` | `unpinned` | Baked at build time. A job quoting a different commit is refused with `409`. |
| `REZZTOR_ANSIBLE_INVENTORY` | — | Path to a mounted inventory file. Fallback only: the managed inventory (`PUT /inventory`) is the primary path; an empty managed document falls through to this file. |
| `REZZTOR_ANSIBLE_JOB_TIMEOUT` | `300` | Seconds. |
| `REZZTOR_ANSIBLE_MAX_DEVICES` | `10` | Devices one job may target. |
| `REZZTOR_ANSIBLE_MAX_CONCURRENT_JOBS` | `1` | Plays that may touch the estate at once. Raising it is a decision about the network. |
| `REZZTOR_ANSIBLE_MAX_QUEUED_JOBS` | `20` | How many may wait. Beyond this, `429`. |
| `REZZTOR_ANSIBLE_MAX_JOBS_PER_MINUTE` | `10` | Submissions per rolling minute, across every caller. |
| `REZZTOR_ANSIBLE_OUTPUT_LIMIT` | `20000` | Characters of stdout retained. |
| `REZZTOR_ANSIBLE_STATE_DIR` | `/var/lib/eagent-ansible` | Where the job history lives. Must be writable and must be a volume. |
| `REZZTOR_ANSIBLE_JOB_RETENTION_DAYS` | `90` | Jobs older than this are pruned. `0` disables the window. |
| `REZZTOR_ANSIBLE_JOB_RETENTION_MAX` | `5000` | Rows kept, newest first. `0` disables the cap. |
| `REZZTOR_ANSIBLE_BACKUP_KEEP` | `30` | Config backups kept per host, newest first. |
| `REZZTOR_ANSIBLE_BACKUP_INTERVAL_HOURS` | `0` | When above zero, back up every inventory-managed host on this timer. `0` is off. |

## Credentials

Stored encrypted under `REZZTOR_ANSIBLE_CREDENTIAL_KEY` (32 bytes, base64:
`head -c32 /dev/urandom | base64`), and **never readable back**. There is no
endpoint that returns a secret -- not for an administrator, not for the agent,
not for debugging. A store that can be read back is one bug away from being a
distribution channel; an operator who has lost a device password should rotate it.

What you get instead is a fingerprint, which answers the only question anyone
legitimately has about a stored credential: is the value I am holding the value
that is loaded?

With no key configured the executor stores nothing at all, rather than storing it
in the clear. An executor that quietly kept device passwords unencrypted because
a variable was unset is the worst outcome available here, and the one that looks
like it is working.

Two accepted limits (ADR 0044: the model never sees a stored device credential):
the application reaches this service over plain HTTP with a bearer token on `ansible_net`,
and the key and the ciphertext live on the same host. Outbound, one nftables table
(`inet rezztor_guard`, ADR 0079) fences every container: with a network list the executor reaches only the listed
device networks and the DNS servers.

## The inventory

The inventory is managed content: a JSON document edited through `PUT /inventory`
and rendered into a real Ansible inventory when a job runs. It lives here and
nowhere else: not in the application, and not in the runner.

**It names credentials; it never contains them.** A `vars` block carrying
`ansible_password` is refused by name, the same way the job API refuses playbook
content by name. An inventory that accepted secrets would be a second credential
store with none of the first one's properties -- unencrypted, returned by `GET`,
and sitting in whatever the console last rendered.

Secrets are resolved into the job's own private data directory, which is tmpfs
and removed when the job ends. They are never written beside the document they
came from.

A group naming a host that does not exist is refused, because it would resolve to
an empty `--limit` -- a job that runs against nothing and reports success.

Group names are what the agent selects with, so name them for how people talk
about the estate -- `core-switches`, `edge-firewalls` -- rather than for the
vendor.

### The mounted file still works

Mount a directory at `/etc/ansible/inventory`, read-only, and point
`REZZTOR_ANSIBLE_INVENTORY` at the file inside it. A deployment that predates the
managed inventory keeps working: an empty document falls through to the mounted
file rather than rendering an inventory with no hosts in it.

Group names are what the agent selects with, so name them for how people talk
about the estate — `core-switches`, `edge-firewalls` — rather than for the
vendor.

```ini
# /etc/ansible/inventory/hosts
[core_switches]
core-sw-1 ansible_host=10.20.0.11
core-sw-2 ansible_host=10.20.0.12

[sonic_switches]
leaf-1 ansible_host=10.20.1.21

[fortigates]
fw-edge-1 ansible_host=10.20.9.5
```

`ansible_network_os` is what decides which modules can run against a host, and it
belongs in group variables rather than repeated per host:

```yaml
# /etc/ansible/inventory/group_vars/core_switches.yml
ansible_connection: ansible.netcommon.network_cli
ansible_network_os: cisco.ios.ios
ansible_user: "{{ lookup('env', 'IOS_USER') }}"

# group_vars/sonic_switches.yml
ansible_connection: ansible.netcommon.httpapi
ansible_network_os: dellemc.enterprise_sonic.sonic

# group_vars/fortigates.yml
ansible_connection: httpapi
ansible_network_os: fortinet.fortios.fortios
```

**Device credentials.** Prefer an SSH key or an API token mounted as a separate
read-only secret over passwords in the inventory; where a password is
unavoidable, use `ansible-vault` and mount the vault password file too. Nothing
in this repository reads either — the executor hands them to Ansible and the
application never sees them.

**Host keys.** `network_cli` verifies device host keys like any SSH client. Decide
this before the first run rather than discovering it as a failure: either mount a
populated `known_hosts` and keep verification on, or set the policy explicitly in
`ansible.cfg` and know that you have.

## What it does not have yet

`reads/inventory_facts` prints `EAGENT_FACTS <host> <json>` for the inventory's
facts refresh. The line reaches `output` JSON-escaped (it is a debug `msg`) and
passes redaction unchanged; `tests/test_net_redaction.py` checks that.

No inventory and no device credentials are shipped — those are yours to mount.
The catalogue's read entries are written from the collections' documented
interfaces and resolve in this image, but **none has been run against real
hardware** — except the SONiC leg, verified live 2026-09-19 against a community
SONiC VS lab, and the Cisco IOS leg, run 2026-10-02 against a Cisco 3725 (IOS 12.4(15)T14)
for reads, show, ports, backup and the interface-description change: the pipeline works end to end, and the entry bugs it found are
fixed.
Every module a playbook names is now resolved against this image by
`plugins/_ea_ansible/tests/test_net_playbooks_resolve.py`, so "resolves in this
image" is checked rather than asserted. The other platforms are still that live
verification away, and it is the next thing that should happen.
