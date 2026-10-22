# ISO DOX

## Purpose

- Own the bootable TEST ISO: Debian 13's installer image with our payload added, and the host tools the installed
  machine runs. First built on 2026-10-05. This tree holds the smallest form: boot, install with no network,
  and the host tools the installed machine runs.

## Ownership

- `scripts/build_iso.sh` builds the ISO. `pins.env` pins what it fetches; `os-packages.sha256` is the checksum of every
  Debian and Docker package on the ISO; `packages.txt` is the one list of packages (used by the build and the install).
  `tools.Dockerfile` is the throwaway tools container on `TOOLS_BASE`, for the ISO build and for the package builder.
- `cores/<core>.installed` (a BIOS install) and `cores/<core>.uefi.installed` (a UEFI install) are the Debian packages
  a fresh install of a core has, `Package\tVersion\tArchitecture`, sorted (ADR 0092). `scripts/sign_release.py`
  reckons a package's Debian updates from them. `test/test-iso.sh` writes the list of the machine it installed
  (`installed.list`; the uefi run needs `SSH_ON=1`); commit both for a new core before its first package.
- `preseed.cfg`, `grub.cfg`, `isolinux.cfg` are Debian's installer answers and the two boot menus.
- `install-target.sh` runs at the end of the installer: copies the payload, checks it, installs Docker and the host tools.
- `host/` runs on the installed machine: `rezztor-setup` (console tool, answers file, the one apply path, status),
  `rezztor-network` (the networks: questions, interfaces and routes, validator, the four files, keep or go back;
  Python, standard library only), `rezztor-firewall.service` (loads the bundle's network guard before the network comes up, after undoing a
  network change nobody kept), `90-rezztor-network.conf` (the sysctls that go with it), `nginx-rezztor.conf`, and the
  units for first boot and image loading.
- `host/rezztor-update` is the update tool (ADR 0080; Python, standard library only): it takes one signed update
  file, from the console or from the web application's request, or downloads the next one from the update service
  when the application asks (`refresh`, `download`, `update_all`), and is the only thing that installs one.
  `host/rezztor-update.path` and `.service` start it; `host/install-update-tool.sh` puts the tool, the units and the
  release keys on a machine (run by `install-target.sh`, and once by hand on a machine installed before the tool
  existed: `scripts/build_update_bootstrap.sh` makes that file). `keys/*.pub` are the release keys every ISO carries.
  `scripts/sign_release.py` makes the keys and the update packages and is the writer of every field the tool reads.
- `host/rezztor-support` and `host/rezztor-console` are the closed console (ADR 0083): the first shows the
  challenge and checks a support code (Python, standard library only; `scripts/sign_release.py support-code` is the
  writer of what it reads), the second is the login shell `rezadmin` gets and runs the menu whatever it is started
  with. `keys/support/*.pub` are the support keys, and the switch.
- `test/test-iso.sh` installs the ISO in QEMU and proves the product answers: boot loader, installer with no network
  card, restart, first boot from an answers disk, HTTPS, closed ports.
- `test/check-iso-payload.sh` checks a finished ISO without a virtual machine: every file against both checksum lists,
  and every layer inside every image tarball.
- `test/standin/` holds a tiny stand-in image and a `docker` wrapper. `REZZTOR_ISO_STANDIN=1 scripts/build_iso.sh` runs
  the real bundle script with it and writes `rezztor-eagent-STANDIN-<date>.iso`: the same ISO with stand-in images under
  the real names, for `test-iso.sh` on a machine where QEMU has no KVM. It is never the product and never delivered.

## Local Contracts

- **The ISO is a carrier, not a second install path.** It holds the offline bundle exactly as
  `scripts/build_offline_bundle.sh` makes it, and the machine runs that bundle's `install.sh --unattended`. A need of the
  ISO becomes a change to the bundle (as `REZZTOR_BIND_ADDRESS` did), never a second Compose file or installer.
- **No secret on the ISO, and none made at build time.** The console password, the administrator password, the TLS key,
  the SSH host keys and everything in `.env` are made on the installed machine. No licence key is on the ISO.
- **The installer asks only for the disk.** `preseed.cfg` must not answer `partman-auto/disk`, `partman/confirm` or
  `partman/confirm_nooverwrite`, and no boot menu entry may carry them. Only `test-iso.sh` types them, into the boot loader.
- **Debian's signed boot files are not touched.** The build compares them with the source image and stops if one differs.
- **The payload checksum list is the first thing the installed machine checks**, and it installs nothing when one entry
  is missing or wrong. The build therefore refuses to write a list that names a file not in the payload (a list once
  named its own temporary file, and every install would have stopped there). Run `test/check-iso-payload.sh` on every
  ISO before it leaves the build machine.
- **Nothing is fetched on the installed machine.** Packages come from `/var/lib/rezztor/payload/os`. The build fetches
  only what `pins.env` names, into `~/iso-build`, with the tools in a throwaway container.
- **One apply path, one validator.** The console questions, the answers disk (`REZZTOR-ANSWERS`, file `answers.env`) and
  `rezztor-setup apply FILE` all end in `apply()`, which calls `check` for every answer. An answers file is read as
  `KEY=value` lines and never run as shell. Passwords are not saved, logged, or put on a command line.
- **The network has one owner: `host/rezztor-network`.** `rezztor-setup` asks no network question and validates no
  network answer; it runs the tool (`wizard` at first boot, `menu` later, `apply FILE --yes` for an answers file) and
  reads the address back with `network web`. Rules that must hold whoever edits the tool (ADR 0081 for the first
  four):
  - **The first boot sets up one interface and asks nothing else**: the port when there are several, DHCP or a
    typed address. No "how many networks", no role, no name, no test device. A VLAN ID is asked for only after a
    first try failed. Other cards are left alone until "Add an interface".
  - **The console never asks for a role.** `ask_interface` gives a new interface the role devices, adds users and
    SSH only on a yes to one question whose offered answer is no, and leaves the internet role where it is. Roles,
    names and test devices in an answers file keep working; where a file set the web interface and SSH apart they
    are asked apart, so that Enter changes neither.
  - **One default route.** The gateway of an interface that does not lead to the internet is the next hop of the
    routes named on it and is never written as `default`; `0.0.0.0/0` cannot be typed as a route. A route may name
    another gateway of its interface (`SUBNET@GATEWAY`), which must be inside that interface's subnet. A subnet
    named on the internet interface with no gateway of its own is in the list and not in netplan: the default route
    carries it.
  - **A route that changes the way back to the session typing it asks first**, to another interface or to another
    gateway of the same one.
  - One answers file becomes four files that change together or not at all: `/etc/netplan/90-rezztor.yaml` (networkd),
    `/etc/rezztor/networks/networks.json` (the contract the guard and the product read; do not change its shape),
    `/etc/rezztor/guard.json`, `/etc/rezztor/compose.env`. The functions from answers to text are pure and tested in
    `tests/test_network_tool.py`; everything that touches the machine is in the class `Host`.
  - **Every change is "keep or go back".** The files from before are saved, a systemd timer outside the session is
    started before anything is touched, and only an answer "keep" before the time is over stops it. The boot runs
    `rezztor-network revert` too (`rezztor-firewall.service`), because systemd forgets the timer at a restart.
  - A failed check of the guard is never "continue anyway": the change goes back. A gateway, a test device, DNS, NTP
    or a proxy that does not answer is a warning.
  - **One flat network writes no network list** (decided 2026-10-06): the guard and the product then know nothing and
    behave as on any single network, also when that network has routes (`NET_ROUTES`). A first boot that works first
    time may not say VLAN, role or route, and the menu of such a machine may not say VLAN or role; a test reads the
    transcripts.
  - `.env` of the stack is merged line by line, never rewritten, and Compose is called only when a variable changed.
    Adding a device network changes no variable, so it restarts no container.
  - What is not built is said in the tool (`NOT_HERE`) and in the guides: bonds, a second internet uplink, the same
    subnet in two VLANs, jump hosts, inbound traps, syslog or TFTP, jumbo frames, IPv6 on device networks.
  - The screens in `docs/guides/appliance-*.md` are copied from the tool, and a test fails when they differ.
- **An update has one door: `host/rezztor-update`** (ADR 0080). Rules that must hold whoever edits it:
  - Trust is the files in `/opt/rezztor/keys/*.pub` and nothing else. The signature is checked, with `openssl`,
    before one field of the manifest is read; the payload is checked against the signed checksum before it is
    unpacked. A key is added by `install-update-tool.sh`, never replaced or removed.
  - The web application is not trusted. It can write `inbox/package.rezupd` and `inbox/request.json` and read
    `state/state.json`; the request is an id, one of the words in `ACTIONS` and a name, plus a `version` for
    `download` and, read on a UAT machine only, `server` and `ca_pem`. Files from the inbox are opened without
    following a link and copied into `work/` before they are looked at. No container gets the Docker socket.
  - **The update service is the only address the tool calls** (spec of 2026-10-20). A release machine asks
    `https://update.rezztor.com` with the system's CAs, whatever the request says;
    a `-uat` machine takes the request's `server` and `ca_pem` (and keeps them in `work/uat-server.json` for a
    request that names none), else `https://update.uat.rezztor.test`. The proxy is `HTTPS_PROXY`/`NO_PROXY` of the
    stack's `.env`, never the process environment; no redirect is followed. The session is `hello` with the update
    key and the `age`-decrypted challenge, answered only when the plaintext is exactly 64 lowercase hex characters
    (never a decryption oracle); the token is kept in memory only. The service's list (`state.updates`) only says
    what to fetch: a download is size- and SHA-256-checked against it and then goes through `check` like an
    upload, so trust is still the signature, the keys and `fits`. Only `updates.available[0]` is downloaded, only
    when `downloadable`; an interrupted download is continued with `Range`. The list and the file request name the
    formats this tool reads (`formats`, from `FORMATS`, not a second list). The list may carry `blocked` (version,
    reason `format`, the format it needs) when the next package is one this tool cannot read; that is kept in the
    state and shown, and a file the service refuses for format is said like any other refusal.
  - `update_all` loops refresh, download, check, install, restart (which waits for `/api/health`) until nothing is
    left, and stops at the first failure with `state.error` saying how many were installed. The tool runs on the
    host, so the application's restart does not stop it. After an update that replaced `rezztor-update` itself it
    puts the same request back into the inbox and ends, and the path unit runs the new tool, which goes on.
  - **`state.error` is the last refusal, and a later success takes it away.** The page shows it, so a refusal that
    no longer held stayed on the page: in UAT on 2026-10-21 a `too_old` error from an abandoned download was still
    there after Check now had brought the list back fine. A `refresh` that brings the list clears it; the error of
    an update that still waits for the administrator (the phase is then `checked` or `staged`) stays, and so does
    the error of a `refresh` that failed (that one is in `state.updates.error`).
  - `.env` of the stack gets one changed line (`REZZTOR_VERSION`), never a new file. The database is dumped before
    the switch, with the application stopped, and nothing changes when the dump fails.
  - After the switch the product must answer `/api/health` with the new version; otherwise the files, `.env` and,
    when the newest database change moved, the database go back. A restart interrupted by a power cut is finished
    or taken back at the next boot.
  - The application's files travel as a tree and become an image on the machine (`docker run`, `docker commit`,
    no network); the commit must leave the image's command, user, variables and ports as the base has them, and the
    tool compares them. Every package is built on the base release, never on another package. No downgrade.
  - What a package may put on the host is the list `HOST_FILES`, and `scripts/sign_release.py` holds the same list:
    the four host files, the guard (`host/rezztor-guard` → `/opt/rezztor/rezztor-guard`) and seven units
    (`units/<name>` → `/etc/systemd/system/<name>`), nothing else (ADR 0092). They are backed up and put back with
    the old version. After the switch `daemon-reload` when a unit changed, and the guard's two units load the new
    rules (`reload-or-restart rezztor-guard`, `restart rezztor-guard-runner`) before a container starts; a guard that
    does not load takes the update back.
  - **Format.** The tool reads format 3 and 4 (`FORMATS`). A package with Debian's updates, the guard or a unit is format 4, any
    other is written as 3; a tool from before ADR 0092 refuses 4 before it reads anything else. The same list is sent to the
    update service as `formats`, so a later format is not offered to a tool that cannot read it. Do not put a new
    kind of content into a format a tool on a machine already reads: such a tool ignores unknown fields.
  - **Debian's updates** (`os` in the manifest, `.debs` and a `Packages` index under `os/`) go in only after the new
    version answered, never before, and from the payload alone: a temporary sources list with
    `Dir::Etc::sourceparts=/dev/null` and lists and cache of their own, `dpkg --configure -a`, `apt-get update`,
    `apt-get -y --with-new-pkgs upgrade` with `--force-confold --force-confdef`. Nothing is downloaded and no other
    source is read. A failure leaves the new version running and is `state.os.error`; the next package carries the
    same updates. `state.os` says what changed here (dpkg before and after) and `reboot_needed` (a changed
    `REBOOT` package, `/run/reboot-required`, or an earlier need in the same boot id).
  - The appliance restarts (`reboot`, `systemctl reboot`) only when an administrator asks: from the page only while
    `reboot_needed`, at the console (`rezztor-update reboot`) when asked. `update_all` never restarts it.
  - Files from the inbox are copied up to the size that was checked and no further, and a package with a sparse member
    is not a package: the inbox is the application's to write, and both let a small file become a large one in `work/`.
  - Going back always ends by starting the stack. The dump is made before the switch and the switch is not made without
    it, so with no dump there is nothing to put back, whatever the package says it changes.
  - A request that cannot be read (a full disk included) is removed like one that was read: the path unit starts the tool
    for as long as the file exists.
  - A machine whose `.env` does not name a release version (one that ends in `-lab`, or a hand-installed
    stack that says `latest`) is refused in words (`not_a_release`), not compared: `fits` checks the running version
    against `VERSION` before it turns it into numbers.
  - A UAT machine (ADR 0085) runs `<release>-uat`. A manifest's versions may end in `-uat`, and `fits` requires
    `version`, `base` and `min_from` to carry it exactly when the running version does (`other_kind`), then
    compares without it: a release takes no UAT package and a UAT machine no release package. The signature is
    still checked first, and a UAT machine holds only dummy release keys.
  - Everything that touches the machine is in the class `Host`; `tests/test_update_tool.py` replaces it, and uses the
    real `openssl`, `age` and (for the image) Docker. `test/test-update.sh` is the proof on a real machine.
  - Not built, and said so in ADR 0080: third-party images (search, database) in a package, the update key
    travelling with the licence check. Old kernels are not removed (ADR 0092).
- **A support key on the ISO closes the console, and nothing else does** (ADR 0083). `install-target.sh` does the
  closing in one block that runs only when `keys/support/*.pub` is in the payload: the account's shell becomes
  `rezztor-console`, it leaves the `sudo` group, its one sudo rule is `rezztor-setup` with no argument, SSH forwards
  nothing, and the boot loader's entries cannot be edited (a password made there and kept nowhere, `--unrestricted`
  on the entries, no recovery entries). Rules that must hold whoever edits it:
  - With no support key the installed machine is exactly what it was before this existed. A lab ISO is no longer
    built, so nothing strips the support keys for that kind.
  - The menu runs as root, so **nothing on it may hand over a shell**: no pager, no editor, no command typed by the
    person. The one entry that opens a shell is "Support access", after `rezztor-support verify` said yes.
  - The door is said out loud: the status screen has a line for a closed console, the menu entry is named for what
    it is, and `docs/guides/install-from-iso-vmware.md` tells the customer. Do not add a way in that is not on a
    screen the customer sees.
  - **What the menu does to the product's accounts happens at the console only** (audit 2026-10-16). "A new password
    for a product administrator" is refused when `over_ssh` finds `sshd` among the menu's ancestors (sudo drops
    `SSH_CONNECTION`, and with `use_pty` `tty`/`who -m` see a pts on the console too); a tree it cannot read counts
    as SSH. Over SSH the console password must not be worth every account in the product. The status screen says so.
  - The support bundle goes to a disk lsblk calls removable or USB, never a fixed one, and the screen names it.
  - A code opens once, for the challenge the machine showed, and every opening, closing and refusal is written to
    `/var/log/rezztor/setup.log`. `tests/test_support_access.py` holds the rules of the code and reads the
    installer's block.
  - `test/test-iso.sh` cannot look inside a closed machine. `CLOSED=1` tells it so: it then checks that a command
    sent over SSH is not run, and skips the checks that need a shell.
  - `REZZTOR_ISO_MARK` (4 to 16 capitals and digits, a code and never a name) marks a build made for one customer:
    the ISO's file name, `build-mark` in the payload, `/etc/rezztor/build-mark`, the status screen.
- **`install.sh` runs once.** `apply_stack` runs it only when `/var/lib/rezztor/stack/.env` does not exist; later
  runs only do `docker compose up -d`. The installer itself refuses to replace `.env` or `runner_key` when run
  `--unattended` (it did replace them until 2026-10-11, with new database passwords over the old volume).
- **Both executors run from the first start, with nothing to type** (2026-10-08).
  `apply_stack` hands `COMPOSE_PROFILES=ansible,cloud` to `install.sh`, which makes the secrets and writes the
  profiles into `.env`; the Compose file gives the application the tokens (ADR 0036) and the application switches
  network automation on by itself. The customer enters devices, their credentials and the licence, and no token.
  What Azure changes still need is in the customer's own tenant (two keys, two certificates, one role, the test in
  Settings), and changes stay off until that test passes.
- **The product's port is bound to 127.0.0.1** (`REZZTOR_BIND_ADDRESS` in `.env`), and nginx is the only door. The
  guard's full form forwards nothing from outside to a container, so a port published on another address is closed too.
  nginx keeps no request log (`access_log off;` in `host/nginx-rezztor.conf`): reset links carry their token in the query and
  the MCP and A2A endpoints carry theirs in the path, and the product's own access log, which strips both, is the record.
  Its error log is at level `crit` for the same reason: a request the product did not answer is written at `error`.
  nginx sets `X-Forwarded-For` to the browser's address (set, never added to), and the product believes it from the
  gateway of `app_net` only (`FORWARDED_ALLOW_IPS`, written by `rezztor-setup` and kept by `rezztor-network`), so the
  sign-in throttle counts each browser apart. Request bodies are not buffered (`proxy_request_buffering off;`): the
  product's own limits refuse a body before any of it is on this machine's disk. nginx's own limit is the product's
  largest (`client_max_body_size 5g`, `UPLOAD_LIMIT_BYTES`), so an upload over it is answered 413 by nginx; a body
  over one of the product's smaller limits is answered 503, because the product closes on a body still arriving.
- **One firewall: the bundle's `rezztor-guard`, in its full form** (`/etc/rezztor/guard.json` says `"form": "full"`;
  ADR 0079). The ISO has no firewall script of its own. Two units load the same table: `rezztor-firewall.service` before
  the first interface comes up, and the bundle's `rezztor-guard.service` before Docker, when the DNS servers are known.
  The bundle's `rezztor-guard-runner.service`, after Docker, loads the runner's loopback table into the runner at every
  start of it (ADR 0087).
  Debian's `nftables.service` is masked: its stop action flushes every table, Docker's included.
- **Adding a package means a new `os-packages.sha256`.** The guard added `nftables` and `python3` to `packages.txt` on
  2026-10-06, the network tool `netplan.io` and `iputils-arping`, and the update tool `age` on 2026-10-10; the checksum list is remade by the next build
  (delete it, build, review the diff, commit).
- Every screen says "TEST BUILD, not signed" until milestone M5 (signing) is done.
- **What a customer receives is a RELEASE ISO, built by `scripts/build_release.sh TAG` and by nothing else**
  (ADR 0084). It clones the tag fresh, builds the images there under the version, and runs that clone's
  `scripts/build_iso.sh` with `REZZTOR_ISO_RELEASE=1`, which refuses any tree that is not a clean checkout of the
  tag and any application image not built from it. The file is `rezztor-eagent-<version>.iso`. A release with no
  support key stops unless `REZZTOR_ISO_OPEN_CONSOLE=1` says the console is meant to be open. A TEST ISO is a
  development build: do not hand one over, and do not add a way to make a RELEASE from a working folder.
- **A release ISO is an Enterprise image** (`scripts/build_release.sh` passes `BUILD_LICENCE=enterprise`). The stamp
  check refuses a release whose image does not say `"licence": "enterprise"`. `REZZTOR_ISO_LAB` is refused: a lab ISO
  is no longer built. A source checkout and a self-built image are the Community build.
- **A UAT ISO is for a production-shaped UAT environment and is never handed over** (ADR 0085).
  `REZZTOR_ISO_UAT=1 scripts/build_iso.sh` takes `rezztor/eagent:uat` (built with `BUILD_LICENCE=uat`, a version
  that ends in `-uat` and `REZZTOR_UAT_TRUST`) and writes `rezztor-eagent-UAT-<date>.iso`; it trusts only the dummy
  licence root and the UAT licence server baked into the image. `REZZTOR_ISO_RELEASE_KEYS=<dir>` replaces
  `keys/*.pub` with dummy release keys. Support keys stay (a closed console is tested too). An image carrying
  `licence_trust.json` goes into a UAT ISO and nothing else; a release refuses UAT and `REZZTOR_ISO_RELEASE_KEYS`.
- **Known limit: the installed machine does not start with Secure Boot on.** `preseed.cfg` switches recommended packages
  off, and the signed GRUB is one of them. The comment in the file names the likely fix; it needs a full install test.

## Work Guidance

- A moved Debian point release: change the three `DEBIAN_ISO*` lines in `pins.env`, delete `os-packages.sha256`, build,
  commit the new file. Use the suite `trixie` only: `-security` versions do not match the installer image's packages.
  Security fixes reach installed machines in packages (`sign_release.py package --os-snapshot`, ADR 0092), not here.
- A new core: commit `cores/<core>.installed` (and `.uefi.installed`) from `test/test-iso.sh` before its first
  package; `sign_release.py os-updates --core <core> --snapshot <time>` says what a package would carry.
- After the product images change: `scripts/build_iso.sh --refresh-payload`.

## Verification

- `pytest tests/test_update_tool.py tests/test_update_api.py` for the update tool and the application's side of it. The
  update-service tests run a stand-in service over real HTTPS with a CA made in the test, and a CONNECT proxy.
- `pytest tests/test_iso_build.py tests/test_installer.py tests/test_network_tool.py` (no KVM needed). The last one
  reads the generated netplan files with the real `netplan generate` in a `debian:13` container and skips, saying so,
  when Docker or the image is not there.
- `deploy/iso/test/check-iso-payload.sh <iso>` on the ISO that will be handed over.
- `deploy/iso/test/test-iso.sh <iso> uefi` and `... bios`: both must end with no FAIL line. The machine has two cards;
  the first boot must leave the second alone, and the bios run adds it from the console menu with two routes. With KVM, run them on the
  real ISO. Without KVM, run them on the stand-in ISO and say in the report that the real images were not installed in a
  virtual machine. A screenshot is evidence only when it is newer than the start of the run it is cited for.

## Child DOX Index

No child DOX files.
