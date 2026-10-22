# Deploying Rezztor EAgent

**Docker Compose is how this runs.** One command brings up the application, a
code-execution runner, the Ansible executor, web search and PostgreSQL:
[INSTALL.md](../../deploy/customer/INSTALL.md).

Per-user isolation does not need an orchestrator any more. The runner carries one
Linux account per user, with its own uid and a 0700 home, so two users are two
users to the kernel (ADR 0038).

> **The customer delivery story is being redesigned.** The k3s appliance, the
> bootable installer ISO and the Helm chart were retired on 2026-09-18: none had
> ever been installed anywhere, all three are in the git history, and carrying
> three delivery shapes while the application itself was still moving cost more
> than it returned. How the product reaches a customer is a decision for when the
> application is ready, not a thing to maintain in the meantime.
>
> What survives is the part that is not delivery-specific: the Compose stack, the
> per-user runner accounts, the network segmentation and the network guard.

> **One boundary is not in the compose file.** Everything else here applies
> itself when the stack comes up; the network guard (`rezztor-guard`, ADR 0079)
> is one nftables table on the host, because Docker has no way to express "the
> internet but not the customer's network". An installation that skips it
> starts perfectly and is missing it — [INSTALL.md](../../deploy/customer/INSTALL.md),
> step 6. Its rules are proved in private network namespaces
> (`tests/test_network_guard.py`), not yet beside a real Docker on a real host.

Documented from the manifests in this repository rather than from memory. Where
something has not been exercised end to end, it says so.

---

## Before you start

| | |
|---|---|
| **Docker** | 24+ with the Compose plugin |
| **Disk** | ~35 GB. The application image is around 14 GB and the build cache is larger |
| **RAM** | 8 GB minimum, 16 GB comfortable — embeddings and the vector store are resident |
| **A model** | An API key for a provider, or a local endpoint (ollama, vLLM, LM Studio, llama.cpp) |

The image is large because it carries faiss, sentence-transformers, whisper and
`unstructured`. That is a real cost and worth knowing before you
pull it onto a small node.

---

## Images

There is no registry. Build the images locally (tagged `rezztor/eagent`,
`rezztor/eagent-runner`, `rezztor/eagent-ansible`) or `docker load` them from an
offline bundle made with `scripts/build_offline_bundle.sh`. Pin a date tag
(`YYYY.MM.DD`) in `.env` so an upgrade is a decision rather than a restart.

---

## Running it — Docker Compose, single host

### 1. Build the images

Building from source is the path for development and for anyone working
from this repository. Customers do not pull from a registry. Images come from
the offline bundle, or from an update package on an appliance installed from
the ISO.

```bash
git clone <your-fork-url> rezztor-eagent && cd rezztor-eagent

# or simply: ./scripts/build_local.sh
docker build -t rezztor/eagent-base:latest docker/base/
docker build -t rezztor/eagent:latest --build-arg CACHE_DATE=$(date +%s) .
docker build -t rezztor/eagent-runner:latest -f docker/runner/Dockerfile .
```

The base build is slow the first time and cached afterwards. The application build takes
several minutes, most of it exporting layers.

### 2. Generate the runner key

The application reaches the runner over SSH with a key. Every session in the runner is an
unprivileged account, and it **cannot** accept a password — its sshd refuses password authentication outright — so this
step is not optional.

```bash
cd docker/run
ssh-keygen -t ed25519 -f ./runner_key -N ""
chmod 600 ./runner_key
```

This leaves `runner_key` (private, mounted into the application) and `runner_key.pub`
(public, handed to the runner). The runner never sees the private half.

### 3. Write the environment

```bash
cat > .env <<EOF
REZZTOR_ADMIN_USER=admin
REZZTOR_ADMIN_PASSWORD=$(openssl rand -base64 18)
FLASK_SECRET_KEY=$(openssl rand -hex 32)
RUNNER_AUTHORIZED_KEY=$(cat runner_key.pub)
REZZTOR_DB_PASSWORD=$(openssl rand -hex 24)
REZZTOR_DB_APP_PASSWORD=$(openssl rand -hex 24)
REZZTOR_SEARXNG_SECRET=$(openssl rand -hex 32)
EOF
chmod 600 .env
```

**`FLASK_SECRET_KEY` must be stable.** Regenerating it signs every user out. Compose keeps
it in `.env`, so it survives restarts as long as that file does.

Set `REZZTOR_ADMIN_PASSWORD` and you choose the first password; leave it unset and the
application generates one, prints it once at startup, and requires it to be changed at
first sign-in. Either is fine — the printed one is stronger, the chosen one does not
require reading a log.

### 4. Start it

```bash
docker compose up -d
docker compose logs -f eagent
```

Wait for `Rezztor EAgent is running.` — first boot takes a minute or two while it
initialises the framework and seeds the account store.

It is on **http://localhost:50080**.

### 5. Confirm it actually works

Signing in is not proof. Code execution is what exercises the runner, the key and the SSH
path:

```bash
docker compose exec runner id            # expect uid=0(root): that is sshd; sessions are eagent-NNN accounts
docker compose logs runner | head -3     # expect "Server listening on 0.0.0.0 port 2222"
```

Then sign in and ask the agent to run something trivial in the code execution tool. If it
fails, the runner key is the usual reason — check that `RUNNER_AUTHORIZED_KEY` in `.env`
matches `runner_key.pub` exactly, including no trailing newline mangling.

### What the compose path gives you, and what it does not

**Does:** the application and code execution in separate containers, agent code as an
unprivileged account on a read-only root filesystem (sshd keeps five capabilities, sessions none), resource and
pid limits, durable state in a named volume.

**Also does:** per-user runner isolation. The runner carries a fixed pool of unprivileged
Linux accounts and the application claims one per user, so two people running code are two
uids with two `0700` homes (ADR 0038).
The pool pins the runner's host key before the first connection, so that path is
`StrictHostKeyChecking=yes` and not trust-on-first-use.

**Does not:** a CPU or memory limit per user, or a private PID namespace. There is one
runner container, so a heavy job slows the others down and process names are visible
between accounts. `/tmp` is shared as well: `/workspace/<slot>` is the boundary.

---

## Corporate sign-in (OIDC)

Local accounts work out of the box. To use your directory instead:

Set these in the `.env` beside `docker-compose.yml`, then `docker compose up -d`:

```bash
REZZTOR_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0
REZZTOR_OIDC_CLIENT_ID=<application-id>
REZZTOR_OIDC_CLIENT_SECRET=<secret>
REZZTOR_OIDC_REDIRECT_URI=https://eagent.example.internal/auth/callback
REZZTOR_OIDC_ROLE_MAP=eagent-admins=admin,eagent-power=power,eagent-users=user
```

There is no `REZZTOR_OIDC_ENABLED`: a deployment has an identity provider when it has an
issuer and a client id, and has local accounts when it does not. The compose file names
each of these variables in the `eagent` service, which is what carries them into the
container — a variable written into `.env` and not named there reaches Compose's own
interpolation and never reaches the application, and the symptom is a sign-in page that
looks exactly as it did before.

Four things to know:

- **An identity in no mapped group is refused**, not given a default role. That is
  deliberate — admitting an unknown identity at low privilege is still admitting it.
- **Roles are re-read from group membership on every sign-in.** Removing someone from a
  group demotes them at their next login with nothing to do here.
- **Entra ID often sends group object IDs rather than names.** If sign-in succeeds but
  everyone is refused, that is why — either map the GUIDs in `REZZTOR_OIDC_ROLE_MAP` or
  configure the group claim to emit names. Only Keycloak has been exercised end to end.
- **Leaving a variable set but empty is the same as not setting it**, so the block above
  can be declared in full and filled in as needed.

The local form stays available at `/login?local=1` as a break-glass path for an IdP outage.

---

## Operating it

**Backups.** Two volumes and two files. `eagent_usr` holds per-user files — uploads,
downloads, workdirs, skills — and the `postgres` volume holds everything else: accounts,
settings, conversations, memory, scheduled tasks. Back them up together with `.env` and
`runner_key`, which are outside both: the data is useless without `FLASK_SECRET_KEY`, and
restoring one without the other signs everyone out of a deployment they can no longer
reach the runner from.

**Upgrades.** There is no registry to pull from. A hand-installed Compose
deployment is upgraded by loading the new images and setting `REZZTOR_VERSION`,
then:

```bash
docker compose up -d --wait
```

Images come from the offline bundle, or from an update package on an appliance
installed from the ISO. Only that appliance has the update tool. The application
has a healthcheck, and `--wait` holds until it passes, so traffic is not routed
to it until it answers.

**Scaling.** One host, one deployment. The compose file pins `container_name`, and more
than one replica would need shared storage **and** a Redis adapter for socket.io — without
both, WebSockets break in ways that look like intermittent UI failures.

---

## When it does not work

| Symptom | Cause |
|---|---|
| Blank page after sign-in | Serving the app somewhere other than `/app`. The router is mounted there; served elsewhere it matches no route and renders nothing |
| `503`, "the web interface has not been built" | `frontend/dist` is missing. The image builds it; a source checkout does not |
| Code execution fails immediately | Runner key mismatch, or `ssh`/`ssh-keygen` missing from the image |
| Everyone signed out after a restart | `FLASK_SECRET_KEY` is not stable, or the cookie name changed |
| The container is up but refuses connections | It is still starting. The compose healthcheck is what `--wait` holds on; `docker compose ps` shows it |
| OIDC works, everyone refused | Group claim carries object IDs, not names |

Logs: `docker compose logs -f eagent`.
Audit events are JSON lines on stdout prefixed `REZZTOR_AUDIT` — ship them off-box, because
the copy in the application is not a trail.

---

## Local development

Not a deployment, but the fastest way to look at it:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
.venv/bin/python run_ui.py
```

http://localhost:5000. The frontend must be built or the server returns 503 — there is no
development fallback interface. Code execution runs locally unless a runner is configured,
which is convenient and is exactly what `REZZTOR_REQUIRE_REMOTE_EXECUTION` exists to
prevent in production.

For anything involving the code execution tool, run the stack rather than the host
instance: `docker/run/docker-compose.yml` is the development equivalent of the customer
one, and code execution builds container paths that do not exist outside it.
