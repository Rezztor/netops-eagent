# Deploy DOX

## Purpose

- Own how Rezztor EAgent is run: the Docker Compose stack, the installer that
  brings it up, and the boundaries that are not inside the compose file.
- Keep customer-facing delivery distinct from internal/dev paths.

## Ownership

- `customer/` owns the Compose stack a customer runs, `install.sh` /
  `installer.sh`, the offline-bundle helpers, and the network guard (`rezztor-guard`).
- `dev/` owns pinned tooling used by developers, not customers.
- `iso/` owns the bootable TEST ISO (2026-10-05): a carrier that installs Debian and Docker with no network and then
  runs the same bundle and `install.sh`, and the appliance's network tool (`iso/host/rezztor-network`), which writes
  the guard's two input files. See [iso/AGENTS.md](iso/AGENTS.md).

## Local Contracts

- **There is one deployment shape: Docker Compose.** The k3s appliance, the
  bootable installer ISO and the Helm chart were retired on 2026-09-18. Nothing
  had ever been installed anywhere, and carrying three delivery shapes while the
  application was still moving cost more than it returned. They are in the git
  history. How the product reaches a customer is a decision for when the
  application is ready — do not re-add a second install path in the meantime.
  The TEST ISO under `iso/` (2026-10-05) is not a
  second shape: it carries the offline bundle unchanged and runs its
  `install.sh`. Since 2026-10-13 what a customer receives is that ISO built as a release, from a tag in a fresh
  clone (`scripts/build_release.sh`, ADR 0084); a TEST ISO is a development build.

- **Nothing here fetches from a Rezztor host.** `install.rezztor.com`, the page
  that served the installer and the script that published it were removed on
  2026-09-18 for the same reason as the appliance: the delivery decision is
  deferred, and a hosted address maintained in the meantime is a promise about
  a shape nobody has chosen. `install.sh` and `installer.sh` read the files
  beside them — `scripts/build_offline_bundle.sh` is what puts them there — and
  `REZZTOR_INSTALL_URL` is the explicit opt-in for a host, not a default. The one
  exception is the appliance's update tool, which asks the update service
  (`update.rezztor.com`) for signed packages when the administrator's application
  asks it to (spec of 2026-10-20; `iso/AGENTS.md`); installing never fetches.

- **Nothing is generated at build time.** The session key, both database
  passwords and the Ansible tokens are written on the customer's machine, once,
  by `installer.sh` into `.env`. Generate them on the builder and every customer
  shares all of them. An update must merge into that file, never replace it —
  rewriting it mints a new session key, signing out every account, and database
  passwords the application cannot use.

- **The application has two folders of the host mounted for updates, and no more power than before** (ADR 0080):
  `${REZZTOR_UPDATE_DIR:-/var/lib/rezztor/updates}/state` read-only and `.../inbox` read-write, in
  `customer/docker-compose.yml`. On the appliance the root tool `iso/host/rezztor-update` writes the first and reads
  the second; on a hand install nothing does, the folders stay empty and the Updates page says there is no update
  service. Do not mount the tool's `work` folder, the release keys or the Docker socket into any container.

- **The application container is not root once it serves** (ADR 0022, amended 2026-10-16). Its service in both
  compose files has `cap_drop: ALL`, exactly `CHOWN, DAC_READ_SEARCH, SETUID, SETGID` added for the start-up
  hand-over to uid 1000, and `no-new-privileges`; the container port is 8080, the host port is unchanged. A host
  tool that writes inside it uses `docker exec -u` (root there has no `DAC_OVERRIDE`). The update inbox is
  `root:1000 0770`. `tests/test_deployment_hardening.py` holds all of it.

- Do not bake licence keys into images. An Enterprise image asks the operator to paste the key after first
  sign-in. A Community build has no key.

- **The guard is the one boundary that is not in the compose file, and there is
  one of it** (ADR 0079). `customer/rezztor-guard` generates and loads a single
  nftables table, `inet rezztor_guard`, from the network list
  (`/etc/rezztor/networks/networks.json`, optional) and `/etc/rezztor/guard.json`
  (optional). It has to be loaded on the host: an installation that skips it
  starts perfectly and is missing it. `INSTALL.md` says so, the installer puts
  it beside the compose file with a pinned hash, and tests assert the bundle
  carries it. Rules that must hold whoever edits it:
  - It only drops, never flushes the rule set, and loads as one transaction.
    An `iptables` chain or a table outside the guard is a second fence: do not
    add one. The guard's own file holds two tables, `inet rezztor_guard` and
    `bridge rezztor_guard`: traffic between two containers on one bridge
    reaches the inet hooks only with `br_netfilter`, so what must hold there
    (on `rz-runner`, a connection to the runner's sshd and nothing else) is in
    the bridge table (ADR 0087).
  - Inside the runner, `rezztor-guard runner` loads `inet rezztor_runner` into
    the runner's own network namespace: the slot uids (the `eagent-NNN`
    accounts in the runner's own `/etc/passwd`, cross-checked against its
    `RUNNER_SLOT_BASE_UID`/`RUNNER_SLOTS`) reach nothing over
    loopback but Docker's resolver. Docker makes a new namespace at every
    start, so `rezztor-guard-runner.service` (after Docker) follows Docker's
    events with `--watch`; a load that fails ends the watch with an error and
    systemd retries it with a backoff, and the appliance's status screen says
    so (`guard_state` in `iso/host/rezztor-setup`). It finds the runner by the compose
    `container_name`; `tests/test_deployment_hardening.py` holds the two
    together. The bundle, the installer, the guide and the ISO ship and enable
    both units.
  - **Small form** (default, a hand install): only what enters or leaves our
    bridges is judged. No rule may touch the customer's other traffic or close
    a port on their server. **Full form** (`"form": "full"`, the appliance):
    also no routing between customer networks, and the host is closed except
    443, 80, ping and SSH while `/etc/rezztor/ssh-on` exists.
  - A list it cannot use never leaves the host unfenced: the last good rules
    stay loaded, or the no-list rules are.
  - Only a network whose roles are exactly `["devices"]` is closed to the
    application (set `dev_only4`). A network that is also for the internet or
    for users stays as open to it as without a list, and the executor may
    enter it (`dev_net4`). Do not merge the two sets.
  - On the appliance an update package may bring a new guard and its units (ADR 0092): the update tool loads the
    new rules before a container starts and the old ones again when it goes back to the old version.
  - Every rule change needs a check in `tests/network_guard_proof.sh`, which
    runs the rules in private network namespaces for three VLANs and for one
    flat network. Run it with the guard off too: every "blocked" check must
    then be open, or the check proves nothing.

- **The compose files name the bridges and the block the guard is written
  against.** Every network has a fixed bridge name (`rz-app`, `rz-ansible`,
  `rz-runner`, `rz-search`, `rz-cloud`, `rz-backend`, `rz-clouddb`) and a /24
  inside `${REZZTOR_NET_PREFIX:-172.31}.248.0/21`. The application is not on
  `ansible_net` (socket only) and has its own outbound network with
  `gw_priority`, so `rz-ansible` carries the executor alone.
  `tests/test_deployment_hardening.py` holds the names, the block and both
  compose files together; the installer refuses a block this host already uses.

- **Per-user isolation is uids, not orchestration.** The runner carries one
  Linux account per user, each with a 0700 home, and sshd resolves `%u` before
  reading `authorized_keys` so a key registered for one slot cannot open
  another's session (ADR 0038). The pool is fixed at the size the runner
  image was built with; exhaustion refuses rather than sharing an account.

- The application container writes each user's public key into a volume the
  runner reads. That volume carries public keys only — the host key's private
  half stays in `runner_state`, which the application does not mount.

- **The executor is reached over a socket, and the runner does not get it.** The
  `ansible_socket` volume is mounted by the application and the executor only
  (both compose files); the executor listens on that socket and on no TCP port
  (ADR 0051). The tokens still apply. The credential key and tokens may be
  supplied as `*_FILE` secrets instead of environment variables.

- **The cloud executor is its own container behind the `cloud` profile** (ADR 0061, 0068). It runs reviewed OpenTofu
  against Azure with keys the application does not hold, so it has two networks of its own (`cloud_net` out to Azure,
  `cloud_db` internal to the database only), two volumes only it and the application touch (`cloud_state` is the
  executor's alone, `cloud_socket` is shared with the application), and the runner gets neither. The installer generates
  its five secrets (`REZZTOR_CLOUD_JOB_TOKEN`, `_ADMIN_TOKEN` which must differ, `_KEY`, `_STATE_PASSPHRASE`,
  `REZZTOR_DB_CLOUD_PASSWORD`); an installation that predates them appends them, never replaces `.env`. Losing the
  passphrase makes the state unreadable, so `INSTALL.md` puts it in the backup. `tests/test_deployment_hardening.py`
  asserts the networks and volumes. Narrowing the executor's egress to Microsoft's hosts is the customer's firewall
  (`INSTALL.md` lists them); no script here does it.
