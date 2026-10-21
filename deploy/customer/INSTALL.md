# Installing Rezztor EAgent

Roughly 20 minutes, most of it spent loading large images from the bundle. You do not need the
source code and will not build anything.

## The short version

From the directory Rezztor sent you, which holds `install.sh`, `installer.sh`
and the files they need:

```bash
sha256sum install.sh      # compare with the checksum in Rezztor's email
less install.sh           # read it before you run it
sh install.sh
```

The installer asks what it needs, generates the rest, and tells you the address
and password at the end. It checks disk, ports and Docker *before* downloading
anything, so it fails in seconds.

Everything it does is below, if you would rather do it by hand.

---

## What you should have received

| From Rezztor | What it is |
|---|---|
| A SHA256 for `install.sh` | So you can check the script against something that did not come from the same place |
| The delivery bundle | `install.sh`, `installer.sh`, the Compose file, the network guard, and this guide |
| A release version, e.g. `2026.09.08` | Which build you are entitled to run |
| An activation key, e.g. `REZ-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX` | Activates the product once it is running |

If any of those are missing, stop and ask before going further.

## What you need on the machine

| | |
|---|---|
| **Docker** | 28 or newer, with the Compose plugin 2.33.1 or newer (`docker compose version`) |
| **For the guard (step 6)** | `python3` and `nft` (package `nftables`) on the host |
| **Disk** | 35 GB free. The application image alone is ~11 GB |
| **RAM** | 8 GB minimum, 16 GB comfortable |
| **A model** | An API key for a provider, or a local endpoint (Ollama, vLLM, LM Studio, llama.cpp) |

The image is large because it carries the vector store, embedding models,
speech-to-text, document parsing, and a browser. That is a real cost and worth
knowing before you pull it onto a small node.

---

## 1. Load the images

```bash
docker load -i images/eagent-<version>.tar.gz
docker load -i images/eagent-runner-<version>.tar.gz
```

## 2. Verify what you received

```bash
sha256sum -c SHA256SUMS
```

Every line must say `OK`. If any does not, the media is damaged or the
bundle was altered in transit -- stop and ask for a fresh copy.

## 3. Generate the runner key

The two containers talk over SSH with a key, never a password. Generate the
pair once, in this directory:

```bash
ssh-keygen -t ed25519 -f ./runner_key -N ""
```

This produces `runner_key` (private, mounted read-only into the application)
and `runner_key.pub` (public, handed to the runner). **Keep the private half
as you would any other credential.**

## 4. Write the environment file

Create `.env` beside the Compose file:

```bash
cat > .env <<EOF
REZZTOR_VERSION=2026.09.08
REZZTOR_ADMIN_USER=admin
REZZTOR_ADMIN_PASSWORD=$(openssl rand -base64 24)
FLASK_SECRET_KEY=$(openssl rand -hex 32)
RUNNER_AUTHORIZED_KEY=$(cat runner_key.pub)
REZZTOR_DB_PASSWORD=$(openssl rand -hex 24)
REZZTOR_DB_APP_PASSWORD=$(openssl rand -hex 24)
REZZTOR_SEARXNG_SECRET=$(openssl rand -hex 32)
EOF
chmod 600 .env

# Note the administrator password -- you need it to sign in
grep REZZTOR_ADMIN_PASSWORD .env
```

Three of these matter more than they look:

- **`FLASK_SECRET_KEY` must stay the same forever.** Change it and every user
  is signed out on the next restart.
- **The two database passwords must stay the same forever too.** The data
  volume is initialised with them on the first start; a different value later
  locks the application out of its own data.
- **`REZZTOR_ADMIN_PASSWORD` is a seed for first boot only.** Change it in the
  interface once you are in, then it no longer matters what is in this file.

## 5. Start it

```bash
docker compose up -d
```

The first start takes a few minutes while the containers initialise. Watch it
if you want:

```bash
docker compose logs -f eagent
```

**Give the runner's workspace a size of its own.** Every user's files live in
one Docker volume (`runner_workspace`), and Compose cannot limit its size. The
runner limits each account's processes, the memory of each process and the
size of one file (2 GB), but an account can still write files until Docker's
disk is full, and the database is on that disk. Put a ceiling on it, either:

- `/var/lib/docker` on a filesystem of its own, so a full workspace stops the
  containers and not the machine; or
- on XFS mounted with project quotas (`prjquota`), a quota on the volume's
  folder:

```bash
dir=$(docker volume inspect -f '{{ .Mountpoint }}' "$(docker volume ls -q | grep runner_workspace)")
sudo xfs_quota -x -c "project -s -p $dir 4242" /var/lib/docker
sudo xfs_quota -x -c "limit -p bhard=50g 4242" /var/lib/docker
```

## 6. Load the network guard

**Do this before anyone uses it.** Everything else in this guide is in the
compose file and applies itself; this one is not, and an installation without it
starts perfectly and is missing a boundary.

The runner is the container that executes agent-written code. It needs the
internet — agent code installs packages — but it has no business reaching your
management network, your other servers, or a cloud metadata endpoint. The same
is true of the search engine and of the cloud executor. Docker alone cannot
express that: a bridge network sends everything out through the host, so
whatever the host can reach, a container can reach.

`rezztor-guard` is a firewall of its own on the host (nftables: the tables
`inet rezztor_guard` and `bridge rezztor_guard`). It needs `python3` and `nft`
on the host, and it runs as root:

```bash
sudo install -D -m 0755 rezztor-guard /opt/rezztor/rezztor-guard
sudo cp rezztor-guard.service rezztor-guard-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rezztor-guard rezztor-guard-runner
```

What it does on your server, and what it leaves alone:

- The runner, the search engine and the cloud executor reach public addresses
  only: not `10/8`, `172.16/12`, `192.168/16`, `100.64/10`, and not `169.254/16`,
  which is the metadata address that hands out credentials on a cloud instance.
  They do not reach services on this host either.
- Nothing from outside is passed on to a container, except the web port the
  compose file publishes.
- The runner does not open connections to the application on the network
  they share; only the application opens one, to the runner.
- Inside the runner, one user's code does not reach a server another user's
  code started on its loopback. Docker makes the runner's network anew at
  every start, so `rezztor-guard-runner` loads that rule again each time; it
  needs `nsenter` (package `util-linux`) on the host.
- The application and the Ansible executor reach what they reached before.
- Traffic that does not touch these containers is not judged at all: your other
  containers, your own firewall rules and the services on this server stay as
  they are. The guard only ever drops, and never removes a rule of Docker's or
  of yours.

It is loaded before Docker starts, and a Docker restart or upgrade does not
remove it. Three things need a reload (`sudo systemctl reload rezztor-guard`):

- **Your DNS servers changed.** The fenced containers may ask the DNS servers
  in this host's resolver file, and no other private address. The guard reads
  that file when it loads.
- **You use a proxy on a private address.** List it in `/etc/rezztor/guard.json`,
  or the runner and the cloud executor cannot reach it:
  `{"infra": [{"address": "10.0.30.8", "protocol": "tcp", "port": 3128}]}`
- **Your own firewall tooling ran `nft flush ruleset`.** That removes every
  table, this one included.

On a host without systemd, run `sudo /opt/rezztor/rezztor-guard apply` at every
boot, before Docker starts, and keep `sudo /opt/rezztor/rezztor-guard runner --watch`
running once Docker is up. `rezztor-guard print` shows the rules without
loading anything.

**If you ran the earlier `runner-firewall.sh`:** the guard replaces it. Remove
the old unit and its rules once the guard is loaded:

```bash
sudo systemctl disable --now rezztor-runner-firewall
sudo rm -f /etc/systemd/system/rezztor-runner-firewall.service /opt/rezztor/runner-firewall.sh
sudo iptables -D DOCKER-USER -s 172.31.250.0/24 -j REZZTOR-RUNNER
sudo iptables -D INPUT -s 172.31.250.0/24 -j REZZTOR-RUNNER
sudo iptables -F REZZTOR-RUNNER && sudo iptables -X REZZTOR-RUNNER
```

Check it took:

```bash
sudo nft list table inet rezztor_guard | head
# Inside the runner, the loopback table (loaded again at every start of it):
sudo nsenter -t "$(docker inspect -f '{{.State.Pid}}' rezztor-eagent-runner)" -n nft list table inet rezztor_runner

# From inside the runner: the internet answers, your network does not.
# Python rather than nc, because the runner carries an interpreter for the
# agent's code and does not carry netcat.
docker compose exec runner /opt/runner-venv/bin/python3 -c '
import socket
for label, host, port in (("internet", "1.1.1.1", 443),
                         ("your network", "192.168.1.1", 443),
                         ("cloud metadata", "169.254.169.254", 80)):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); print(label, "REACHABLE")
    except Exception:
        print(label, "blocked")
    finally:
        s.close()
'
```

`internet REACHABLE` and everything else `blocked` is what a fenced runner
looks like. If you reach your estate over a mesh VPN -- NetBird, Tailscale --
test one of those addresses too: they live outside the usual private ranges,
and the guard covers them explicitly for that reason.

If name resolution stops working in the runner after this, the guard does not
know your DNS server: check `sudo nft list set inet rezztor_guard infra` against
the resolver file, and reload.

## 7. Sign in

Open **http://localhost:50080/app** (or the host's address on port 50080) and
sign in with the administrator credentials from `.env`.

Change the administrator password immediately: **Users**, in the left-hand
navigation.

## 8. Activate your licence

**Settings → Licence**, type the activation key Rezztor sent you into
**Activation key**, and press **Activate**. The product checks the key for
typing mistakes before it sends anything.

The product talks to one host, `https://license.rezztor.com`, over HTTPS only.
It contacts it once to activate, then once a day in the background to renew
its 30-day lease. What it sends is the key (at activation), a random
installation id, the licence number and version, the installation token it was
given at activation, the number and a hash of the lease it holds, a seat count,
the product version and a random nonce, and nothing else. No conversation
content, no user names, no files, no host names, no device addresses.

If the licence server is unreachable, the product keeps working on the lease
it holds, then through a grace period (everything works, with a warning). With
the defaults that is about 60 days from the last successful check-in. After
that the installation is Restricted: sign-in, reading, export, settings and
the licence page work; new agent runs, new device or cloud changes and
scheduled tasks do not start. Nothing is deleted. **A problem at Rezztor's end
does not become an outage at yours.**

To move the key to another server, press **Release this installation** on the
old one, then activate on the new one.

## 9. Point it at a model

**Models**, in the left-hand navigation. Either an API key for a hosted provider, or the address
of a model server you run yourself. The application can reach services on the
host machine as `host.docker.internal`, which is how you keep inference
on-premises.

---

## Putting TLS in front of it

This stack serves plain HTTP on `REZZTOR_PORT`, and ships no certificate
handling of its own. Every sign-in, every conversation and the session cookie
cross the network in the clear until you put a reverse proxy in front of it —
so do that before anyone signs in from another machine.

Any proxy will do. It needs to pass `X-Forwarded-Proto`, which is what tells the
application to mark the session cookie `Secure`. If yours does not send that
header, set this in `.env` instead and the flag is forced:

```
SESSION_COOKIE_SECURE=true
```

Set it only once TLS is actually in front: a `Secure` cookie is not stored by a
browser on a plain-HTTP page, so sign-in stops working rather than silently
becoming insecure.

If the proxy serves a different hostname than the one users type, list the
origins the browser will use:

```
REZZTOR_ALLOWED_ORIGINS=https://eagent.example.internal
```

The application also needs to know who is asking. Behind a proxy every browser
reaches it from the proxy's address. While `FORWARDED_ALLOW_IPS` is empty the
sign-in form therefore does not count wrong passwords per address for a private
peer, only per address and username, so one person's failures lock one account
rather than the form for everyone. Naming the proxy turns the per-address count
back on against the real client address. Have the proxy set `X-Forwarded-For` to the
client's address (set it, do not append to what the browser sent), and name the
proxy's address, as the application sees it, in `.env`:

```
FORWARDED_ALLOW_IPS=172.31.249.1
```

`172.31.249.1` is right for a proxy on this host (with `REZZTOR_NET_PREFIX=10.99`
it is `10.99.249.1`); for a proxy on another machine it is that machine's
address. Set it only when the port cannot be reached without passing the proxy
(`REZZTOR_BIND_ADDRESS=127.0.0.1` for a proxy on this host): whoever reaches
the port from the address named here chooses the address the application
believes.

## Keeping the audit trail

Audit events are JSON lines on **stdout**, prefixed `REZZTOR_AUDIT` — one per
sign-in, account change, tool run and network change. Nothing is written to a
file on purpose: a record an administrator can edit is not evidence of what an
administrator did.

That means **a deployment that does not collect stdout keeps no audit trail at
all.** Point your log shipper at the container, or configure the forwarder in
Settings → SIEM forwarding.

```bash
docker compose logs -f eagent | grep REZZTOR_AUDIT
```

The stack rotates container logs at 50 MB × 5 files so they cannot fill the
disk, which also means the oldest events are eventually dropped. Ship them
off-box if you need to keep them.

Alongside the audit events the same stream carries application lines, each
stamped with an RFC3339 UTC time so they line up with the audit records, and one
access line per request:

```
INFO:     10.0.0.4:54770 - "GET /api/poll?<redacted> HTTP/1.1" 200 OK
```

The query string is deliberately not recorded. It is where a password-reset
token and a user's own file paths travel, and this log is kept and forwarded.
Turn the access lines off in **Settings → Interfaces** if you would rather not have
them.

## Upgrading

```bash
# load the new images, edit .env to the new version, then
docker compose up -d
```

Take `docker-compose.yml` from the new bundle too, in place of yours. From the
release after 2026.10.16 the application listens on port 8080 inside its
container and is not root there; with an older compose file it answers on no
port Compose maps and its health check fails. `.env` and `runner_key` stay.

Take the guard from the new bundle as well, and from the release after
2026.10.19 its second unit, which keeps the runner's accounts off each other's
loopback; an installation without it starts perfectly and is missing that:

```bash
sudo install -D -m 0755 rezztor-guard /opt/rezztor/rezztor-guard
sudo cp rezztor-guard.service rezztor-guard-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl reload rezztor-guard
sudo systemctl enable --now rezztor-guard-runner
systemctl status rezztor-guard-runner     # active (running), and its log says "loaded the loopback rules"
```

**Back up first** — see below. Migrations only move forward: if you roll
`REZZTOR_VERSION` back afterwards, the old application refuses to start against
the newer database. A restore is the way back.

A machine installed from the ISO does all of this itself, from one signed file:
`sudo rezztor-update`, or Settings, Updates in the browser. It backs the database
up, switches, and goes back when the new version does not answer.

Your data survives the upgrade — accounts, settings, conversations, memory and
uploads, and your licence activation. It lives in **two** volumes, not one.

Pin `REZZTOR_VERSION` to a date rather than tracking `latest`, so an upgrade
is a decision you make rather than something a restart does to you.

## Backing up

There are two volumes and two files, and a backup missing any of them is not a
backup. Accounts, settings, conversations, scheduled tasks and memory are in
PostgreSQL; `usr` holds each user's files, uploads and skills. This section used
to capture `usr` alone and call it everything durable, which was true before the
accounts moved to the database — a restore from one of those comes back with all
the files and nobody able to sign in.

```bash
# Compose names volumes after this directory, so find them rather than
# guessing -- a hard-coded name backs up nothing and says nothing.
USR_VOLUME=$(docker inspect rezztor-eagent \
  -f '{{ range .Mounts }}{{ if eq .Destination "/eagent/usr" }}{{ .Name }}{{ end }}{{ end }}')
echo "backing up $USR_VOLUME and the database"

# The database, as SQL. Dumped from the running server rather than copied as
# files: a file copy of a live data directory is not guaranteed to restore.
docker compose exec -T postgres pg_dump -U eagent -d eagent \
  > eagent-database-$(date +%F).sql

# The files.
docker run --rm -v "$USR_VOLUME":/data -v "$PWD":/backup alpine \
  tar czf /backup/eagent-usr-$(date +%F).tar.gz -C /data .
```

Back up `.env` and `runner_key` too — they are not in either volume, and you
need both to bring the deployment back. `.env` holds the database passwords: a
dump you cannot decrypt the deployment for is not a restore either.

If you run the Azure changes (below), two more things belong in the backup:
`REZZTOR_CLOUD_STATE_PASSPHRASE` and `REZZTOR_CLOUD_KEY` in `.env`. The state of
what the product made in Azure is in the database, but only as ciphertext: the
passphrase is the one thing that opens it, and it is deliberately not in the
database dump. Lose it and the product can no longer plan a change to what it
made; the resources stay in Azure, untouched. The `cloud_state` volume holds the
sealed Azure keys; if it is lost you make the two keys again in Settings and
upload the new certificates.

To restore, bring up `postgres` alone, load the SQL into an empty `eagent`
database, unpack `usr` into its volume, then start the rest. Practise it once
before you need it; an untested backup is a guess.

---

## Optional: network automation

Off by default on a hand install; a machine installed from the ISO starts it by
itself. The Ansible executor is the only container allowed to reach your
management network, and it holds the device credentials.

1. A new install does steps 1 and 2 by itself when it is run as
   `COMPOSE_PROFILES=ansible sh install.sh`. To switch it on later, add the
   three secrets to `.env` once (see `.env.example`; never replace `.env`):
   executor token, admin token (must differ) and credential key.
2. Start it, now and at every later `docker compose up`: add the line
   `COMPOSE_PROFILES=ansible` to `.env` (`ansible,cloud` with the Azure
   changes), then `docker compose up -d`.
3. Nothing to type in the interface: the application reaches the executor over
   a Unix socket in a shared volume (ADR 0051), is given both tokens by the
   Compose file (ADR 0036), and switches the plugin on by itself the first time
   it starts beside an executor. **Settings → Network automation** shows both
   tokens as supplied by the deployment.
4. To keep the credential key out of `.env`, mount it as a Docker secret and set
   `REZZTOR_ANSIBLE_CREDENTIAL_KEY_FILE` instead; see
   `docs/guides/security-credentials.md`. To keep the administration token away
   from the application, see `REZZTOR_ANSIBLE_APP_ADMIN_TOKEN` in `.env.example`.

Full walkthrough: [Network automation setup](../../docs/setup/network-automation.md).

---

## Optional: Azure changes

Off by default on a hand install; a machine installed from the ISO starts the
executor by itself, and what is left there begins at step 3. The cloud executor
is the only container that can change anything in Azure: it runs reviewed OpenTofu through two providers, with keys the
application does not hold. **It never deletes, stops, restarts, resizes or
replaces anything**, and it runs a change only after an engineer has entered a
CR or SR number. Read [Azure changes](../../docs/guides/azure-changes.md) first:
it says what to create in Entra and Azure, and what the product will refuse
whatever it is asked.

1. The installer writes the five secrets into `.env`. On an installation that
   predates this, add them once (never replace `.env`, which would sign every
   user out):

   ```bash
   cat >> .env <<EOF
   REZZTOR_CLOUD_JOB_TOKEN=$(openssl rand -hex 32)
   REZZTOR_CLOUD_ADMIN_TOKEN=$(openssl rand -hex 32)
   REZZTOR_CLOUD_KEY=$(openssl rand -base64 32)
   REZZTOR_CLOUD_STATE_PASSPHRASE=$(openssl rand -hex 32)
   REZZTOR_DB_CLOUD_PASSWORD=$(openssl rand -hex 24)
   EOF
   ```

   Restart the application once so it creates the database role the executor
   connects as: `docker compose up -d eagent`.
2. Start the executor: `docker compose --profile cloud up -d`.
3. In the UI, Settings → Azure changes: make the two keys, upload the two
   certificates to Entra, give the change app the role in the guide, set the
   guard rails (subscriptions, resource groups, regions), and press **Test**.
   Changes stay off until the test says the change app holds that role and no other.

**Outbound access.** The executor needs HTTPS (443) to `management.azure.com`,
`login.microsoftonline.com` and `prices.azure.com`. A storage account template
may also need the new account's own data-plane host
(`<name>.blob.core.windows.net` and its siblings), which is a wildcard on most
firewalls. It needs no access to `registry.opentofu.org`, `github.com` or any
download host: OpenTofu and both providers are inside the image, pinned by
SHA-256, and the executor is configured to refuse any provider that is not.
It sits on its own network (`cloud_net`) and reaches the database on another
that has no route out (`cloud_db`); the runner and the application are on
neither. If your firewall fences the runner (the guard of step 6 does), leave the executor's
network alone: it does not carry model-written code.

---

## If something is wrong

**The site does not load.** Check both containers are running
(`docker compose ps`) and look at `docker compose logs eagent`.

**Sign-in fails.** The administrator password is seeded only on the very
first start. If the volume already existed, the password in `.env` is not what
the account has.

**The agent cannot run code.** The runner is unreachable. Confirm
`RUNNER_AUTHORIZED_KEY` in `.env` matches the contents of `runner_key.pub`
exactly, and that `runner_key` is present in this directory. This is
deliberately fail-closed: rather than quietly executing code inside the
application container, it refuses.

**A licence warning appears.** The installation has not been able to check in
for some time. Check outbound HTTPS to `https://license.rezztor.com` (proxy,
firewall, DNS), then press **Check in now** in Settings → Licence. The page
shows the last check-in and its result. If the state is Restricted and
that does not help, ask Rezztor.

**Everything is slow on first use.** The embedding model is in the image and
nothing is downloaded; its first use loads it into memory, which takes a while.

---

## Support

Send `docker compose logs eagent --tail 200` and the version shown at
the top of the **Overview** screen with any report. The version identifies the exact build,
which is usually the first thing worth knowing.
