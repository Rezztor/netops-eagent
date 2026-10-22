"""Deployment security properties.

These read the deployment artefacts rather than running them. They exist because
each property was true once and is easy to lose silently: a package added back
to a Dockerfile, a capability restored to make something work, a port reopened
for debugging.
"""

import importlib.machinery
import importlib.util
import ipaddress
import pathlib
import re
import socketserver
import threading
import wsgiref.simple_server

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(*parts) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the application container must not execute code ------------------------

def test_application_container_runs_no_shell_server():
    """Code execution moved to the runner, so the container holding everyone's
    data has no reason to accept shell connections."""
    # supervisord is gone with the root path (ADR 0022, amended 2026-10-16);
    # there is no process table left for an sshd or cron entry to hide in.
    assert not (ROOT / "docker" / "run" / "fs" / "etc" / "supervisor").exists()
    assert not (ROOT / "docker" / "run" / "fs" / "exe" / "supervisor_event_listener.py").exists()
    assert "exec /exe/run_eagent.sh" in read("docker", "run", "fs", "exe", "initialize.sh"), "the application itself must still run"

    assert "EXPOSE 22 " not in read("Dockerfile")
    assert not (ROOT / "docker" / "base" / "fs" / "ins" / "configure_ssh.sh").exists()
    assert not (ROOT / "docker" / "run" / "fs" / "ins" / "setup_ssh.sh").exists()


def test_base_image_is_debian_not_kali():
    """Kali ships an offensive security toolchain that enterprise image scanning
    will flag and that the application never used."""
    base = read("docker", "base", "Dockerfile")
    # Ignore comments: the file explains what it replaced, so the old image name
    # appears in prose. What matters is the FROM.
    directives = "\n".join(
        line for line in base.splitlines() if not line.strip().startswith("#")
    )
    assert "FROM debian:stable-slim" in directives
    assert "kalilinux" not in directives


def test_base_image_does_not_install_an_ssh_server():
    packages = read("docker", "base", "fs", "ins", "install_base_packages2.sh")
    assert "openssh-server" not in packages


# --- the runner must be unprivileged ----------------------------------------

def test_runner_refuses_root_login_and_names_one_account():
    dockerfile = read("docker", "runner", "Dockerfile")
    assert "'PermitRootLogin no'" in dockerfile
    # eagent plus the slot pool. A slot is a real Linux account, which is what
    # makes one user's files unreadable to another however they arrange their own
    # session -- see the ADR superseding 0019. The glob is deliberately narrow:
    # it admits eagent-NNN and nothing else the image happens to carry.
    assert "'AllowUsers eagent eagent-*'" in dockerfile
    # sshd resolves %u before reading the file, so a key registered for one slot
    # cannot open another slot's session. This is the binding between key and
    # user, and it is the whole per-user boundary on a shared runner.
    assert "'AuthorizedKeysFile /run/runner/keys/%u/authorized_keys'" in dockerfile
    # Forwarding would turn the runner into a network pivot.
    assert "'AllowTcpForwarding no'" in dockerfile
    assert "'PermitTunnel no'" in dockerfile


def test_runner_creates_an_unprivileged_user_without_sudo():
    dockerfile = read("docker", "runner", "Dockerfile")
    assert "useradd" in dockerfile
    assert "sudo" not in dockerfile.split("RUN useradd")[1].split("\n")[0]


def test_runner_refuses_to_start_without_a_credential():
    """A runner that came up with no credential would accept nobody, which is a
    silent failure, or with a default one, which is worse.

    The credential is now a public key rather than a password: the runner runs
    unprivileged and cannot set a password on its own account, and its sshd
    refuses password authentication outright (ADR 0019).
    """
    entrypoint = read("docker", "runner", "entrypoint.sh")
    assert 'if [ -z "${RUNNER_AUTHORIZED_KEY:-}" ]' in entrypoint
    assert "exit 1" in entrypoint


def test_runner_refuses_to_start_as_root():
    """The premise of every other restriction on the runner pod. Asserted in the
    entrypoint as well as the pod spec, so an operator running the image by hand
    cannot quietly opt out of it."""
    assert 'if [ "$(id -u)" = "0" ]' in read("docker", "runner", "entrypoint.sh")


def test_runner_generates_its_own_host_keys():
    """A host key baked into the image would be shared by every runner, letting
    one impersonate another -- exactly the substitution the application's host
    key checking exists to catch."""
    entrypoint = read("docker", "runner", "entrypoint.sh")
    assert "ssh-keygen -q -t ed25519" in entrypoint
    # Written into the runtime volume, not the image: the root filesystem is
    # read-only, so anywhere else would fail at startup.
    assert '${RUNTIME_DIR}/ssh_host_ed25519_key' in entrypoint


# --- compose ----------------------------------------------------------------

def test_compose_separates_the_application_from_execution():
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    # Three, and each for a different reason: the application executes no agent
    # code, the runner executes it with nothing worth reaching, and the Ansible
    # executor holds device credentials and is the only one with a route to the
    # estate. The runner's egress to private ranges is denied, so Ansible is a
    # separate container rather than a route opened on the runner (ADR 0030).
    assert set(compose["services"]) == {
        "eagent",
        "runner",
        "ansible",
        # Azure changes (ADR 0061): reviewed OpenTofu behind a socket, with the
        # keys that change Azure. Its own container, behind the `cloud` profile.
        "cloud",
        "postgres",
        # Web search. Its own container since the decomposition was finished --
        # it used to run inside the application image, which is why it worked on
        # the root path and silently did not on the unprivileged one.
        "searxng",
    }

    runner = compose["services"]["runner"]
    assert "ports" not in runner, "the runner must not be published to the host"
    assert runner["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in runner["security_opt"]
    assert runner["pids_limit"] and runner["mem_limit"] and runner["cpus"]

    ansible = compose["services"]["ansible"]
    # Still no ports, now that there is something that could be published. The
    # break-glass console binds inside the container when
    # REZZTOR_ANSIBLE_PUBLIC_PORT is set, which reaches `docker compose exec` and
    # a port-forward and nothing else. Putting it on the host is a second,
    # deliberate edit -- and this assertion is what makes it deliberate.
    assert "ports" not in ansible, "the executor must not be published to the host"
    assert ansible["read_only"] is True
    assert ansible["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in ansible["security_opt"]
    assert ansible["pids_limit"] and ansible["mem_limit"]
    # Off unless asked for: it has nothing to talk to until an inventory exists.
    assert ansible["profiles"] == ["ansible"]
    # No token, no service. The executor refuses everything rather than
    # accepting everything when it is unconfigured.
    assert "REZZTOR_ANSIBLE_EXECUTOR_TOKEN" in ansible["environment"]
    # Exactly one writable path outlives the container, and it is the job
    # history. `read_only: true` above is what makes that worth asserting:
    # anything else the executor writes goes to tmpfs and is gone on restart.
    # The other is the socket volume the application reaches it by (ADR 0051).
    # The third is the network list, a host directory it can only read.
    assert ansible["volumes"] == [
        "ansible_state:/var/lib/eagent-ansible",
        "ansible_socket:/run/ansible-socket",
        "${REZZTOR_NETWORKS_DIR:-/etc/rezztor/networks}:/etc/rezztor/networks:ro",
    ]
    assert "ansible_state" in compose["volumes"]

    postgres = compose["services"]["postgres"]
    # No ports. Everything durable except uploads lives in here, and publishing
    # it would put the whole instance's state on the host network.
    assert "ports" not in postgres, "the database must not be published to the host"
    assert postgres["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert "healthcheck" in postgres, (
        "the application waits on this; without a healthcheck it starts before "
        "the database accepts connections and fails its first migration"
    )

    app = compose["services"]["eagent"]
    env = app["environment"]
    assert env["REZZTOR_REQUIRE_REMOTE_EXECUTION"] == "true"
    assert env["REZZTOR_RUNNER_HOST"] == "runner"
    # Two roles, two secrets. The application must never be handed the owner's
    # credentials: that role is not subject to row-level security.
    assert "REZZTOR_DB_URL" in env and "REZZTOR_DB_APP_PASSWORD" in env


def test_the_application_does_not_connect_as_the_database_owner():
    """The owner role runs migrations and is not subject to row-level security.
    If the application's password variable ever pointed at it, isolation would
    be off and everything would still appear to work."""
    compose = read("docker", "run", "docker-compose.yml")
    assert "REZZTOR_DB_APP_PASSWORD" in compose
    # The app password variable and the owner password variable are different
    # names; a deployment that set them to the same value is its own decision,
    # but the file must not do it for them.
    assert 'REZZTOR_DB_APP_PASSWORD: "${REZZTOR_DB_PASSWORD' not in compose


def test_the_executor_state_volume_is_not_shared_with_anything_else():
    """The job history carries device output. Nothing else mounts it.

    The runner in particular: ADR 0030 exists because a container that runs
    model-authored code must not be able to read what the executor saw.
    """
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    mounted_by = [
        name
        for name, service in compose["services"].items()
        if any(str(volume).startswith("ansible_state:") for volume in service.get("volumes") or [])
    ]
    assert mounted_by == ["ansible"]


def test_compose_refuses_to_start_without_real_secrets():
    """`:?` makes the variable required, so a deployment cannot come up with a
    blank administrator password, an unstable session key, or a runner nobody
    can reach.

    RUNNER_AUTHORIZED_KEY replaced RUNNER_PASSWORD when the runner moved to key
    authentication: it runs unprivileged and cannot set a password on its own
    account, so a password was not merely unused but impossible.
    """
    compose = read("docker", "run", "docker-compose.yml")
    for variable in ("REZZTOR_ADMIN_PASSWORD", "FLASK_SECRET_KEY", "RUNNER_AUTHORIZED_KEY"):
        assert f"{variable}:?" in compose, variable
    assert "RUNNER_PASSWORD" not in compose, (
        "a password the runner will refuse is worse than no password at all"
    )


# --- host-run development mode ----------------------------------------------

def test_a_call_through_the_old_development_link_runs_here_and_nothing_serves_the_link():
    """Starting a chat once failed with "No RFC password": a process outside the container forwarded these calls
    to a paired instance. The link is gone (2026-10-11). Its endpoint ran any module's function for the holder of
    a pre-shared key; what is left of it is a name that runs the function it is given."""
    import asyncio
    from pathlib import Path

    from helpers import runtime

    def probe(value):
        return value * 2

    async def aprobe(value):
        return value * 3

    assert asyncio.run(runtime.call_development_function(probe, 21)) == 42
    assert asyncio.run(runtime.call_development_function(aprobe, 14)) == 42
    root = Path(__file__).resolve().parents[1]
    assert not (root / "api" / "rfc.py").exists() and not (root / "helpers" / "rfc.py").exists()
    assert not hasattr(runtime, "handle_rfc")


def test_the_compose_services_agree_on_how_to_reach_each_other():
    """The application and the runner are configured in the same file and were
    still contradicting each other.

    When the runner moved to key authentication on an unprivileged port, its own
    service was updated and the application's was not: it still dialled port 22,
    still supplied a password the runner would refuse, and had no path to a
    private key at all. The stack came up, looked healthy, and could not execute
    a line of agent code.

    Nothing caught it because every existing check reads one service or the
    other. This one reads both and compares.
    """
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    app = compose["services"]["eagent"]
    runner = compose["services"]["runner"]

    dialled = str(app["environment"]["REZZTOR_RUNNER_PORT"])
    exposed = [str(p) for p in runner["expose"]]
    assert dialled in exposed, f"the app dials {dialled}, the runner exposes {exposed}"

    # Key authentication on both sides, or neither.
    runner_wants_key = "RUNNER_AUTHORIZED_KEY" in runner["environment"]
    app_has_key = "REZZTOR_RUNNER_IDENTITY_FILE" in app["environment"]
    assert runner_wants_key == app_has_key, (
        "one side expects a key and the other does not"
    )
    assert "REZZTOR_RUNNER_PASSWORD" not in app["environment"], (
        "the runner refuses password authentication, so supplying one is misleading"
    )

    # The identity file has to actually be mounted, or the app authenticates
    # with a path that does not exist.
    identity = app["environment"]["REZZTOR_RUNNER_IDENTITY_FILE"]
    mounted = [v for v in app.get("volumes", []) if v.split(":")[1:2] == [identity]]
    assert mounted, f"{identity} is configured but never mounted into the container"
    assert mounted[0].endswith(":ro"), "the private key should be mounted read-only"


def test_the_runner_private_key_never_reaches_the_runner():
    """The runner needs the public half and must never hold the private one."""
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    runner = compose["services"]["runner"]
    assert "RUNNER_AUTHORIZED_KEY" in runner["environment"]
    # Match the source exactly. `runner_keys` -- the volume carrying each user's
    # *public* key for sshd -- contains this name as a substring, and a loose
    # check here fails on it while saying the private key leaked.
    sources = {entry.split(":")[0] for entry in (runner.get("volumes", []) or [])}
    assert "./runner_key" not in sources, "the private key is mounted into the runner"


# --- the runner's host key must survive a restart on Compose -----------------


def test_the_compose_runner_keeps_its_host_key_across_restarts():
    """A regenerated host key breaks code execution until someone intervenes.

    The entrypoint generates a key per container and prints the public half so
    the caller can pin it. The retired pod pool read that from the pod's logs, so a fresh
    pod is pinned afresh. Compose has no such reader -- the application pins with
    `accept-new`, which accepts an unknown key and refuses a *changed* one -- so
    on a tmpfs every restart and every image update stopped execution with "host
    key verification failed" until usr/.runner_known_hosts was deleted by hand.
    """
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    runner = compose["services"]["runner"]

    tmpfs_paths = {entry.split(":", 1)[0] for entry in runner.get("tmpfs", [])}
    assert "/run/runner" not in tmpfs_paths, (
        "/run/runner is a tmpfs again, so the host key is regenerated on every "
        "restart and the application's pin goes stale"
    )

    targets = {entry.split(":")[1] for entry in runner.get("volumes", []) if ":" in entry}
    assert "/run/runner" in targets
    named = {entry.split(":")[0] for entry in runner.get("volumes", []) if ":" in entry}
    assert named <= set(compose.get("volumes") or {}), "the runner volume is not declared"


def test_the_compose_runner_keeps_work_but_never_shares_it():
    """The workspace is durable now, and that is a deliberate reversal.

    It used to be a tmpfs, and the reason given was that a surviving workspace
    would leak one conversation's leftovers into the next on a shared runner.
    What changed is *who* the runner is shared with: each user now holds their
    own Linux account with a 0700 home, so the leak that mattered -- one user
    reading another's -- is refused by the kernel rather than by throwing
    everybody's files away. Verified live on 2026-09-18.

    The volume must therefore be a named one, not a tmpfs: Docker seeds an empty
    named volume from the image, ownership included, which is how the per-slot
    homes arrive owned 0700 by their own account. A tmpfs would cover them and
    every session would land in a directory that does not exist.
    """
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    runner = compose["services"]["runner"]

    tmpfs_paths = {entry.split(":", 1)[0] for entry in runner.get("tmpfs", [])}
    assert {"/tmp", "/home/eagent"} <= tmpfs_paths
    assert "/workspace" not in tmpfs_paths, (
        "a tmpfs here hides the per-slot homes baked into the image"
    )

    targets = {entry.split(":")[1] for entry in runner.get("volumes", []) if ":" in entry}
    assert targets == {"/run/runner", "/workspace", "/run/runner/keys"}, (
        f"the runner has durable storage it was not given: {targets}"
    )


def test_the_shared_temp_directories_are_not_an_isolation_boundary():
    """`/tmp` is shared by all 128 slots, and nothing in a mount can fix that.

    The per-slot 0700 homes cover `/workspace` and nothing else. `/tmp` and
    `/home/eagent` are one tmpfs each, necessarily world-writable because 128
    uids write to them, so at the default 022 umask agent code writing a file
    there published it to every other user on the runner. `_wipe()` empties the
    home and not `/tmp`, so the residue outlived its owner's account too.

    What actually separates the contents lives in the image, so this asserts
    both halves: the mount states the sticky mode it was silently defaulting to,
    and the image ships the umask and per-slot TMPDIR that make the shared mount
    survivable.
    """
    for where in (("docker", "run"), ("deploy", "customer")):
        compose = yaml.safe_load(read(*where, "docker-compose.yml"))
        entries = compose["services"]["runner"]["tmpfs"]
        for entry in entries:
            path, _, options = entry.partition(":")
            assert "mode=1777" in options, (
                f"{'/'.join(where)}: {path} leaves its mode to the tmpfs default. "
                "State it, so the next reader does not have to know what the default is."
            )

    dockerfile = read("docker", "runner", "Dockerfile")
    assert "/etc/profile.d/10-slot-session.sh" in dockerfile
    assert "'umask 077'" in dockerfile, (
        "without it, a tool that hardcodes /tmp instead of honouring TMPDIR "
        "still writes 0644 into a directory 127 other accounts can read"
    )
    assert 'TMPDIR="$HOME/tmp"' in dockerfile, (
        "the ordinary case belongs inside the slot's own 0700 home"
    )


def test_the_runner_can_stop_a_departing_accounts_processes():
    """`release()` stops the outgoing account before its uid goes to somebody else.

    It did that with `pkill`, which is a package: without procps the command was
    not found, the kill degraded to nothing and `release()` still reported
    success. It now sends `kill -9 -1`, a shell builtin that needs no package
    and that a forking process cannot outrun. procps stays in the image for the
    code that runs there (`ps`, `pgrep`, `pkill`).
    """
    pool = read("plugins", "_code_execution", "helpers", "runner_pool.py")
    assert '"kill -9 -1"' in pool, "release() no longer stops the outgoing account's processes"
    assert "procps" in read("docker", "runner", "Dockerfile")


def test_the_shared_account_does_not_log_in_on_a_slot_runner():
    """In slot mode `eagent` is reached by nothing, and it is the group that
    may write every slot's key directory: a session as it could put a key of
    its own in front of any slot (agent-safety review S10). sshd_config admits
    it for single mode, so the entrypoint denies it when it serves slots --
    with DenyUsers, because a second AllowUsers adds to the first."""
    entrypoint = read("docker", "runner", "entrypoint.sh")
    slots = entrypoint[entrypoint.index('if [ "${MODE}" = "slots" ]; then\n    exec /usr/sbin/sshd'):]
    assert slots.splitlines()[1].strip() == 'exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config -o "DenyUsers=${RUNNER_USER}"'
    assert slots.splitlines()[3].strip() == "exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config", "single mode lost its account"


def test_sshd_does_not_execute_a_file_from_the_users_home():
    """`PermitUserRC` defaults to yes, and `StrictModes` is off.

    sshd would run `~/.ssh/rc` on every login without checking its permissions,
    so any account that could write into another's home -- through the shared
    `/home/eagent`, or a home whose previous occupant left it world-writable --
    could have sshd execute a script as the next user to connect. Nothing here
    uses the feature, so it is the cheapest of the three to close.
    """
    sshd = read("docker", "runner", "Dockerfile")
    assert "'PermitUserRC no'" in sshd


def test_a_command_sent_to_a_slot_reads_nothing_from_its_home(tmp_path):
    """The wipe is a command the application sends as the slot it is emptying.

    sshd runs it as `<the account's shell> -c <command>`, and Debian's bash
    reads `~/.bashrc` first when sshd started it. A `.bashrc` holding `exit 0`
    made the kill and the wipe do nothing and answer success, so the next
    account was handed the last one's files, its running processes and that
    `.bashrc`. Shown against a real sshd in the runner image before this test
    was written; here the slot shell itself is run with such a home.
    """
    import os
    import shutil
    import subprocess

    shell = ROOT / "docker" / "runner" / "slot-shell"
    assert shell.is_file(), "the slot accounts have no shell of their own"
    if not shutil.which("bash"):
        pytest.skip("needs bash")
    for name in (".bashrc", ".bash_profile", ".bash_login", ".profile"):
        (tmp_path / name).write_text("echo RC-RAN\nexit 0\n", encoding="utf-8")
    # What sshd gives a command: its own variables, and nothing of the client's.
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "SSH_CLIENT": "10.0.0.9 5 2222"}

    done = subprocess.run(
        ["sh", str(shell), "-c", "echo WIPE-RAN"], env=env, capture_output=True, text=True, timeout=30
    )

    assert done.stdout.strip() == "WIPE-RAN", "a file in the account's home ran before the command"


def test_every_slot_session_starts_with_limits_of_its_own(tmp_path):
    """One container, one budget: without limits one account could take every
    process and all the memory, and nobody else could even log in (agent-safety
    review S7). There is no PAM, so the slot shell sets them, soft and hard,
    for a command and for a login session alike. Run here: the limits the shell
    hands on are read back from bash (the image's build reads them too)."""
    import os
    import shutil
    import subprocess

    if not shutil.which("bash"):
        pytest.skip("needs bash")
    shell = str(ROOT / "docker" / "runner" / "slot-shell")
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    done = subprocess.run(
        ["sh", shell, "-c", "ulimit -Hu; ulimit -Hv; ulimit -Hf"], env=env, capture_output=True, text=True, timeout=30
    )
    if done.returncode and "prlimit" in done.stderr:
        pytest.skip(f"prlimit did not run here: {done.stderr.strip()}")
    assert done.stdout.split() == ["256", "1500000", "2097152"], done.stdout + done.stderr
    lines = read("docker", "runner", "slot-shell").splitlines()
    assert next(i for i, line in enumerate(lines) if line.startswith("prlimit ")) < lines.index('if [ "$#" -eq 0 ]; then')
    # Two accounts at their limit must not fill the container (security review, 2026-10-19).
    # dev/ is the maintainers' local stack and is not in every checkout.
    for parts in (("deploy", "customer", "docker-compose.yml"), ("docker", "run", "docker-compose.yml"),
                  *([("dev", "docker-compose.yml")] if (ROOT / "dev").is_dir() else [])):
        assert yaml.safe_load(read(*parts))["services"]["runner"]["pids_limit"] >= 16 * 256, "/".join(parts)


def test_the_runner_gives_every_slot_account_that_shell():
    dockerfile = read("docker", "runner", "Dockerfile")
    assert "COPY docker/runner/slot-shell /usr/local/bin/slot-shell" in dockerfile
    slots = dockerfile[dockerfile.index("for i in $(seq 0 $((RUNNER_SLOTS - 1)))"):]
    assert "--shell /usr/local/bin/slot-shell" in slots[: slots.index("done")], (
        "a slot account whose shell is bash runs its own ~/.bashrc before the wipe"
    )
    shell = read("docker", "runner", "slot-shell")
    assert "--noprofile --norc" in shell


def test_the_runner_volumes_the_application_may_not_see():
    """Two of the runner's three volumes must stay inside it.

    `runner_state` holds `ssh_host_ed25519_key` -- the private half of the host
    key the application pins. Mounting it into the application would put the
    secret that proves the runner's identity in the container that verifies it.

    `runner_workspace` holds every user's home. The application writing there
    would sidestep the uids that keep those homes apart.

    `runner_keys` is deliberately shared, and is the exception this test exists
    to keep narrow: it carries public keys only, written by the application and
    read by sshd, and the per-slot `authorized_keys` binding is the whole
    per-user boundary. If anything else ever becomes shared, it should have to
    be argued for here.
    """
    compose = yaml.safe_load(read("docker", "run", "docker-compose.yml"))
    private_to_the_runner = {"runner_state", "runner_workspace"}

    for name, service in compose["services"].items():
        if name == "runner":
            continue
        mounted = {entry.split(":")[0] for entry in service.get("volumes", []) if ":" in entry}
        leaked = mounted & private_to_the_runner
        assert not leaked, f"service {name} mounts {sorted(leaked)}"


def test_the_offline_bundle_carries_every_image_the_compose_file_names():
    """An air-gapped site cannot fetch what the bundle forgot, and the failure
    arrives at `docker compose up` on a host with no route to a registry.

    The bundle shipped without the Ansible executor for a while precisely
    because nothing checked this.
    """
    import re

    compose = read("deploy", "customer", "docker-compose.yml")
    bundle = read("scripts", "build_offline_bundle.sh")

    named = set(re.findall(r"^\s*image:\s*(\S+)", compose, re.M))
    for image in named:
        # Our own images are registry-prefixed and versioned; match on the
        # repository name, which is what the bundle script writes.
        repository = image.split(":")[0].split("/")[-1]
        assert repository in bundle, (
            f"{image} is in the Compose file and not in the offline bundle"
        )


def test_the_postgres_image_is_pinned_to_one_version_everywhere():
    """Three files name it. A bundle built against a different tag than the
    Compose file asks for loads an image nothing starts."""
    import re

    pins = set()
    for parts in (
        ("deploy", "customer", "docker-compose.yml"),
        ("docker", "run", "docker-compose.yml"),
        ("deploy", "customer", "installer.sh"),
        ("scripts", "build_offline_bundle.sh"),
    ):
        found = re.findall(r"pgvector/pgvector:\S+?(?=[\"'\s]|$)", read(*parts))
        pins.update(found)
    assert len(pins) == 1, f"the Postgres image is pinned inconsistently: {sorted(pins)}"


# --- the database -----------------------------------------------------------





















# --- web search runs in its own container ------------------------------------

def test_the_application_image_no_longer_bundles_a_search_engine():
    """It ran inside the application container, started by supervisord.

    supervisord only runs on the root path, so wherever the application runs
    unprivileged as PID 1 nothing bound the search port and every query failed.
    Four separate comments in this repository asserted SearXNG was "its own
    Deployment" while nothing shipped one.

    Splitting it out also removes three things from a production image: an
    account in the sudo group, an unpinned clone of upstream master, and the
    build toolchain the install needed.
    """
    assert not (ROOT / "docker" / "base" / "fs" / "ins" / "install_searxng.sh").exists()
    assert not (ROOT / "docker" / "base" / "fs" / "ins" / "install_searxng2.sh").exists()
    assert not (ROOT / "docker" / "run" / "fs" / "exe" / "run_searxng.sh").exists()

    base = read("docker", "base", "Dockerfile")
    assert "install_searxng" not in base

    # No supervisord.conf remains to start it from; the first test above holds that.
    assert not (ROOT / "docker" / "run" / "fs" / "etc").exists()










# --- the networks, and the guard on the host that is written against them -----
#
# The guard (deploy/customer/rezztor-guard, ADR 0079) names bridges and assumes
# which container sits on which. A name or a subnet changed in a compose file and
# not there produces rules that match nothing, silently, on a deployment that
# otherwise starts perfectly. tests/test_network_guard.py proves what the rules do.

NETWORK_FILES = (("deploy", "customer", "docker-compose.yml"), ("docker", "run", "docker-compose.yml"))
BRIDGES = {
    "app_net": "rz-app", "ansible_net": "rz-ansible", "runner_net": "rz-runner", "searxng_net": "rz-search",
    "cloud_net": "rz-cloud", "backend": "rz-backend", "cloud_db": "rz-clouddb",
}


def _guard():
    path = str(ROOT / "deploy" / "customer" / "rezztor-guard")
    loader = importlib.machinery.SourceFileLoader("rezztor_guard", path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("rezztor_guard", loader))
    loader.exec_module(module)
    return module


def _members(compose: dict, network: str) -> set[str]:
    return {name for name, service in compose["services"].items() if network in (service.get("networks") or [])}


def test_the_guard_finds_the_runner_by_its_compose_name_and_its_uids_in_the_image():
    """The loopback rules go into the container the guard names, for the uids
    the image states (ADR 0087). A container renamed in the compose file, or a
    variable renamed in the image, loads nothing anywhere, silently."""
    guard = _guard()
    compose = yaml.safe_load(read("deploy", "customer", "docker-compose.yml"))
    assert compose["services"]["runner"]["container_name"] == guard.RUNNER
    dockerfile = read("docker", "runner", "Dockerfile")
    for name in ("RUNNER_SLOTS=", "RUNNER_SLOT_BASE_UID="):
        assert name in dockerfile, f"the guard reads {name[:-1]} from the runner's environment"
    unit = read("deploy", "customer", "rezztor-guard-runner.service")
    directives = [line for line in unit.splitlines() if line and not line.startswith("#")]
    assert "ExecStart=/opt/rezztor/rezztor-guard runner --watch" in directives
    assert "After=docker.service rezztor-guard.service" in directives
    assert "Restart=on-failure" in directives and not [line for line in directives if line.startswith("ExecStop")]


def test_every_network_has_a_fixed_bridge_name_and_a_subnet_inside_the_one_block():
    """Five of six networks once took whatever Docker's pool gave them, so a host
    rule could not name them and a customer's own 172.x network could vanish
    behind a bridge. One variable moves the whole block."""
    guard = _guard()
    block = ipaddress.ip_network(guard.BLOCK)
    for parts in NETWORK_FILES:
        where = "/".join(parts)
        networks = yaml.safe_load(read(*parts))["networks"]
        assert set(networks) == set(BRIDGES), where
        subnets = set()
        for name, bridge in BRIDGES.items():
            assert networks[name]["driver_opts"] == {"com.docker.network.bridge.name": bridge}, f"{where}: {name}"
            (config,) = networks[name]["ipam"]["config"]
            match = re.fullmatch(r"\$\{REZZTOR_NET_PREFIX:-(\d+\.\d+)\}\.(\d+)\.0/24", config["subnet"])
            assert match, f"{where}: {name} is not pinned inside the block ({config['subnet']})"
            subnet = ipaddress.ip_network(f"{match.group(1)}.{match.group(2)}.0/24")
            assert subnet.subnet_of(block), f"{where}: {name} {subnet} is outside {block}"
            subnets.add(subnet)
        assert len(subnets) == len(BRIDGES), f"{where}: two networks share a subnet"
        assert {name for name in networks if networks[name].get("internal")} == {"backend", "cloud_db"}, where
    assert set(guard.BRIDGES) == set(BRIDGES.values()), "the guard and the compose files name different bridges"
    assert all(len(name) <= 15 for name in guard.BRIDGES), "an interface name holds 15 characters"
    # The installer checks this same block against the host, and the ISO's network tool refuses an address in it.
    prefix = re.search(r'^DEFAULT_NET_PREFIX="([^"]+)"$', read("deploy", "customer", "installer.sh"), re.M).group(1)
    assert f"{prefix}.248.0/21" == guard.BLOCK
    assert f'DEFAULT_PREFIX = "{prefix}"\n' in read("deploy", "iso", "host", "rezztor-network")


def test_each_bridge_carries_exactly_the_containers_the_guard_judges_it_by():
    """`rz-ansible` may open connections into device networks, so it must carry
    the executor and nothing else; the three fenced bridges must carry the
    container that is fenced; and the application's way out must be its own."""
    guard = _guard()
    assert guard.EXECUTOR == BRIDGES["ansible_net"]
    assert set(guard.FENCED) == {BRIDGES["runner_net"], BRIDGES["searxng_net"], BRIDGES["cloud_net"]}
    assert set(guard.SEALED) == {BRIDGES["backend"], BRIDGES["cloud_db"]}
    for parts in NETWORK_FILES:
        compose = yaml.safe_load(read(*parts))
        where = "/".join(parts)
        assert _members(compose, "ansible_net") == {"ansible"}, f"{where}: something shares the executor's bridge"
        assert _members(compose, "app_net") == {"eagent"}, where
        assert _members(compose, "runner_net") == {"eagent", "runner"}, where
        assert _members(compose, "searxng_net") == {"eagent", "searxng"}, where
        assert _members(compose, "cloud_net") == {"cloud"}, where
        assert _members(compose, "backend") == {"eagent", "postgres"}, where


def test_the_applications_default_route_is_pinned_to_its_own_network():
    """It was on four networks with the same priority, and Docker chose. Had it
    chosen `runner_net`, the fence for user code would have cut the application
    off from every private address."""
    for parts in NETWORK_FILES:
        networks = yaml.safe_load(read(*parts))["services"]["eagent"]["networks"]
        where = "/".join(parts)
        assert networks["app_net"] == {"gw_priority": 1}, where
        assert [name for name, options in networks.items() if options] == ["app_net"], f"{where}: one pinned route"
        # An engine that does not know gw_priority takes the first name in order. That is app_net too.
        assert sorted(networks)[0] == "app_net", where


def test_the_application_is_given_both_tokens_of_the_ansible_executor_unless_the_deployment_says_no():
    """ADR 0036, which the Compose files did not carry over from the chart: a new installation showed "no
    administration token" until somebody pasted one. Set and empty, REZZTOR_ANSIBLE_APP_ADMIN_TOKEN keeps the
    second token away from the application; the executor has its own copy either way."""
    for parts in COMPOSE_FILES:
        services, where = yaml.safe_load(read(*parts))["services"], "/".join(parts)
        application = services["eagent"]["environment"]
        assert application["REZZTOR_ANSIBLE_EXECUTOR_TOKEN"] == "${REZZTOR_ANSIBLE_EXECUTOR_TOKEN-}", where
        assert application["REZZTOR_ANSIBLE_ADMIN_TOKEN"] == "${REZZTOR_ANSIBLE_APP_ADMIN_TOKEN-${REZZTOR_ANSIBLE_ADMIN_TOKEN-}}", where
        assert "ANSIBLE_APP_ADMIN_TOKEN" not in str(services["ansible"]["environment"]), f"{where}: the executor keeps its token"
        # The key that opens the stored device credentials stays with the executor, and the runner gets nothing.
        assert not [name for name in application if "CREDENTIAL_KEY" in name], where
        assert "REZZTOR_ANSIBLE" not in str(services["runner"].get("environment", "")), where
    assert "# REZZTOR_ANSIBLE_APP_ADMIN_TOKEN=" in read("deploy", "customer", ".env.example")


def test_the_web_port_can_be_bound_to_one_address():
    for parts in NETWORK_FILES:
        ports = yaml.safe_load(read(*parts))["services"]["eagent"]["ports"]
        assert ports == ["${REZZTOR_BIND_ADDRESS:+${REZZTOR_BIND_ADDRESS}:}${REZZTOR_PORT:-50080}:8080"], "/".join(parts)


def test_the_application_drops_root_before_it_serves():
    """Audit 2026-10-16, M1: the application was the one container of the stack that ran as root with Docker's
    default capabilities. It starts as root only to hand uid 1000 its writable places (installations made
    before then hold root's files), then drops with setpriv; the compose files leave that root four
    capabilities and no-new-privileges. Proved on the 2026.10.16 image: uid 1000, CapEff 0, NoNewPrivs 1."""
    start = read("docker", "run", "fs", "exe", "initialize.sh")
    root_branch = start.split('if [ "$(id -u)" = "0" ]; then', 1)[1].split("\nfi\n", 1)[0]
    assert "supervisord" not in "\n".join(line.split("#", 1)[0] for line in start.splitlines())
    assert "find /eagent/usr -xdev ! -user eagent -exec chown -h eagent:eagent {} +" in root_branch
    assert "chown root:eagent \"$inbox\" && chmod 0770 \"$inbox\"" in root_branch
    assert root_branch.rstrip().endswith(
        "exec setpriv --reuid=eagent --regid=eagent --clear-groups --no-new-privs -- /exe/initialize.sh \"$BRANCH\"")
    assert "HOME=/home/eagent" in root_branch, "python and its caches must not look for root's home"
    # The console's audit line reopens PID 1's stdout; runc hands stdio over only to a container started unprivileged.
    assert 'chown eagent:eagent "/proc/self/fd/$fd"' in root_branch
    assert "/run/rezztor-update/inbox" in read("deploy", "customer", "docker-compose.yml")
    for parts in COMPOSE_FILES:
        app = yaml.safe_load(read(*parts))["services"]["eagent"]
        where = "/".join(parts)
        assert "user" not in app, f"{where}: the start must be root to fix ownership; it drops by itself"
        assert app["cap_drop"] == ["ALL"], where
        assert sorted(app["cap_add"]) == ["CHOWN", "DAC_READ_SEARCH", "SETGID", "SETUID"], where
        assert "no-new-privileges:true" in app["security_opt"], where
        assert not app.get("privileged") and "network_mode" not in app, where
    # Root in that container holds no DAC_OVERRIDE, so whatever the host runs in it that writes runs as eagent.
    update = read("deploy", "iso", "host", "rezztor-update")
    assert update.count('"docker", "exec", "-i", "-u", "1000:1000", container') == 2
    assert not re.search(r'"docker", "exec", "-i", container', update)
    setup = read("deploy", "iso", "host", "rezztor-setup")
    assert setup.count("docker exec -u eagent rezztor-eagent") == 2 and "docker exec rezztor-eagent" not in setup
    assert "install -d -m 0770 -o root -g 1000 /var/lib/rezztor/updates/inbox" in read(
        "deploy", "iso", "host", "install-update-tool.sh")


def test_the_application_port_is_one_unprivileged_number_everywhere():
    """8080 inside the container, because nothing there may bind below 1024; the host port is unchanged, so nginx
    on the appliance and every guide still point at 50080."""
    assert "EXPOSE 8080 " in read("Dockerfile") and "EXPOSE 80 " not in read("Dockerfile")
    assert 'PORT="${WEB_UI_PORT:-8080}"' in read("docker", "run", "fs", "exe", "run_eagent.sh")
    for parts in COMPOSE_FILES:
        app = yaml.safe_load(read(*parts))["services"]["eagent"]
        assert app["ports"][0].endswith("${REZZTOR_PORT:-50080}:8080"), "/".join(parts)
        assert "http://127.0.0.1:8080/api/health" in app["healthcheck"]["test"][1], "/".join(parts)
        assert "WEB_UI_PORT" not in str(app.get("environment")), "/".join(parts)
    assert "proxy_pass http://127.0.0.1:50080;" in read("deploy", "iso", "host", "nginx-rezztor.conf")
    standin = read("deploy", "iso", "test", "standin", "image", "Dockerfile")
    assert '"-p", "8080"' in standin and "http://127.0.0.1:8080/api/health" in standin


def test_the_application_can_register_a_slot_key_and_the_runner_can_restart_over_it():
    """The application, uid 1000 since 2026-10-16, replaces a slot's authorized_keys; the runner's root has no
    DAC_OVERRIDE. So each slot's directory is root:<shared account> 0775, and the file the application left is
    removed before it is made empty again -- truncating it in place failed and stopped the runner."""
    entry = read("docker", "runner", "entrypoint.sh")
    loop = entry.split('if [ "${MODE}" = "slots" ]; then', 1)[1].split("\nfi\n", 1)[0]
    lines = [line.strip() for line in loop.splitlines()]
    assert 'chown "root:${RUNNER_USER}" "${RUNTIME_DIR}/keys/${slot}"' in lines
    assert 'chmod 0775 "${RUNTIME_DIR}/keys/${slot}"' in lines
    assert lines.index('rm -f "${RUNTIME_DIR}/keys/${slot}/authorized_keys"') < lines.index(
        ': > "${RUNTIME_DIR}/keys/${slot}/authorized_keys"')
    assert "CHOWN" in yaml.safe_load(read("deploy", "customer", "docker-compose.yml"))["services"]["runner"]["cap_add"]


def test_the_proxy_reaches_the_application_the_cloud_executor_and_the_runner_and_not_the_ansible_executor():
    """The Ansible executor reaches devices and nothing else, so it is given no
    way out. An empty proxy variable is no proxy, so the default changes nothing."""
    names = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"}
    for parts in NETWORK_FILES:
        services = yaml.safe_load(read(*parts))["services"]
        where = "/".join(parts)
        for service in ("eagent", "cloud", "runner"):
            env = services[service]["environment"]
            assert names <= set(env), f"{where}: {service}"
            assert env["HTTP_PROXY"] == env["http_proxy"] == "${HTTP_PROXY-}", f"{where}: {service}"
            assert env["HTTPS_PROXY"] == env["https_proxy"] == "${HTTPS_PROXY-}", f"{where}: {service}"
            # The names the stack resolves itself never go through the proxy, whatever the customer adds.
            assert env["NO_PROXY"] == env["no_proxy"], f"{where}: {service}"
            assert env["NO_PROXY"].startswith("localhost,127.0.0.1,searxng,runner,postgres,"), f"{where}: {service}"
            assert env["NO_PROXY"].endswith("${NO_PROXY:+,${NO_PROXY}}"), f"{where}: {service}"
        for service in ("ansible", "postgres", "searxng"):
            assert not names & set(services[service].get("environment") or {}), f"{where}: {service} has a proxy"


def test_the_network_list_is_mounted_into_the_executor_read_only_and_into_nothing_else():
    """The directory, not the file: a list replaced on the host is seen at once.
    A missing directory is every hand install, and means one flat network."""
    mount = "${REZZTOR_NETWORKS_DIR:-/etc/rezztor/networks}:/etc/rezztor/networks:ro"
    for parts in NETWORK_FILES:
        services = yaml.safe_load(read(*parts))["services"]
        holders = {name for name, service in services.items()
                   if any("/etc/rezztor" in str(entry) for entry in service.get("volumes") or [])}
        assert holders == {"ansible"}, "/".join(parts)
        assert mount in services["ansible"]["volumes"], "/".join(parts)
    assert _guard().NETWORKS == "/etc/rezztor/networks/networks.json"


def test_there_is_no_tcp_way_back_to_the_executor():
    """The application left the executor's network, so `http://ansible:8500`
    resolves to nothing. A variable that still offered it would be a switch
    that turns network automation off."""
    for parts in NETWORK_FILES:
        text = read(*parts)
        services = yaml.safe_load(text)["services"]
        where = "/".join(parts)
        assert services["eagent"]["environment"]["REZZTOR_ANSIBLE_EXECUTOR_URL"] == "unix:///run/ansible-socket/executor.sock", where
        # `:-`, so an empty value left in an old .env means the socket and not the TCP listener.
        assert services["ansible"]["environment"]["REZZTOR_ANSIBLE_SOCKET"] == "${REZZTOR_ANSIBLE_SOCKET:-/run/ansible-socket/executor.sock}", where
        assert "ansible:8500" not in text and "REZZTOR_ANSIBLE_EXECUTOR_URL-" not in text, where
    assert "ansible:8500" not in read("deploy", "customer", ".env.example")


class _UnixWsgi(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The real executor application behind a Unix socket, as waitress serves it."""

    daemon_threads = True
    base_environ = {"SERVER_NAME": "unix", "SERVER_PORT": "0", "GATEWAY_INTERFACE": "CGI/1.1", "SCRIPT_NAME": ""}

    def get_app(self):
        from executor import app as executor_app

        return executor_app.app


class _UnixWsgiHandler(wsgiref.simple_server.WSGIRequestHandler):
    def setup(self):
        self.client_address = ("unix", 0)  # an AF_UNIX peer has no address
        super().setup()

    def log_message(self, *args):
        pass


def test_a_read_still_reaches_the_executor_with_the_application_off_its_network(monkeypatch, tmp_path):
    """The compose file's own address, the plugin's own client, the executor's own
    application: a health check and an authenticated read of its job history go
    through the shared socket volume and through nothing else. No network is in
    this test at all, which is the point."""
    from executor import app as executor_app
    from executor import credentials as executor_credentials
    from executor import inventory as executor_inventory
    from executor import store as executor_store
    from plugins._ea_ansible.helpers import config as plugin_config
    from plugins._ea_ansible.helpers import executor_client

    compose = yaml.safe_load(read("deploy", "customer", "docker-compose.yml"))["services"]
    url = compose["eagent"]["environment"]["REZZTOR_ANSIBLE_EXECUTOR_URL"]
    volume = "/run/ansible-socket"
    for service in ("eagent", "ansible"):
        assert f"ansible_socket:{volume}" in compose[service]["volumes"]
    assert url.startswith(f"unix://{volume}/")
    # The volume both containers mount, here a directory both sides of the test share.
    url = url.replace(volume, str(tmp_path))

    monkeypatch.setenv(executor_app.ENV_TOKEN, "a-job-token")
    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    executor_credentials.reset_for_tests(str(tmp_path / "credentials.json"))
    executor_inventory.reset_for_tests(str(tmp_path / "inventory.json"))
    monkeypatch.setattr(plugin_config, "get_config", lambda agent=None: {"executor_url": url})
    monkeypatch.setattr(plugin_config, "executor_token", lambda agent=None: "a-job-token")
    server = _UnixWsgi(url.removeprefix("unix://"), _UnixWsgiHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert executor_client.health()["status"] == "ok"
        assert executor_client.jobs()["jobs"] == []
        # And it is the executor that answered, checking the token: a wrong one is refused.
        monkeypatch.setattr(plugin_config, "executor_token", lambda agent=None: "not-the-token")
        try:
            executor_client.jobs()
        except executor_client.ExecutorRefused:
            pass
        else:
            raise AssertionError("the executor accepted a wrong token")
    finally:
        server.shutdown()
        server.server_close()
        executor_store.reset_for_tests(None)
        executor_credentials.reset_for_tests(None)
        executor_inventory.reset_for_tests(None)


def test_the_application_has_a_healthcheck_the_update_engine_can_fail_on():
    """`compose up --wait` is only as good as the healthchecks it waits on.

    A service with none is treated as satisfied the moment its container is
    running, so an application that starts and exits two seconds later would
    pass the wait -- and update.py's rollback, which fires on that command's
    exit code, would never run. `docker compose up -d --wait` watches readiness. This
    assertion is what replaces it.
    """
    for path in (("docker", "run", "docker-compose.yml"),
                 ("deploy", "customer", "docker-compose.yml")):
        compose = yaml.safe_load(read(*path))
        for service in ("eagent", "postgres"):
            check = compose["services"][service].get("healthcheck")
            assert check and check.get("test"), (
                f"{'/'.join(path)}: {service} has no healthcheck, so a broken "
                "release would be reported as a successful update"
            )


def test_no_profiled_service_requires_a_variable_the_base_stack_does_not():
    """Compose interpolates the whole file before it looks at profiles.

    A `${VAR:?...}` on a service behind a profile therefore stops the *base*
    stack from starting, not the optional one -- `docker compose up -d` dies on
    a variable belonging to a container it was never going to create. That is
    what the ansible executor's token did, and it is how the installer failed
    at its last step. Found the only way this kind of thing is found: by
    running it.

    A variable the base stack already requires is fine, because a deployment
    that has one has the other. The requirement itself is not lost either: a
    service that needs a secret refuses to work without it at runtime, which is
    where the message can say something useful.
    """
    import re

    for parts in (
        ("deploy", "customer", "docker-compose.yml"),
        ("docker", "run", "docker-compose.yml"),
    ):
        where = "/".join(parts)
        blocks = re.split(r"(?m)^  (?=\S)", read(*parts))
        always: set[str] = set()
        profiled: dict[str, set[str]] = {}
        for block in blocks:
            name = block.split(":", 1)[0].strip()
            required = set(re.findall(r"\$\{([A-Z_]+):\?", block))
            if "profiles:" in block:
                profiled[name] = required
            else:
                always |= required
        for name, required in profiled.items():
            unmet = sorted(required - always)
            assert not unmet, (
                f"{where}: service '{name}' is behind a profile but requires "
                f"{unmet} at interpolation time, which blocks the whole stack"
            )


# --- the cloud executor (ADR 0061 to 0065) ----------------------------------

COMPOSE_FILES = (("deploy", "customer", "docker-compose.yml"), ("docker", "run", "docker-compose.yml"))


def _on(compose: dict, network: str) -> set[str]:
    return {name for name, service in compose["services"].items() if network in (service.get("networks") or [])}


def _mounting(compose: dict, volume: str) -> set[str]:
    return {
        name
        for name, service in compose["services"].items()
        if any(str(entry).startswith(f"{volume}:") for entry in service.get("volumes") or [])
    }


def test_the_cloud_executor_is_a_hardened_container_behind_its_own_profile():
    for parts in COMPOSE_FILES:
        cloud = yaml.safe_load(read(*parts))["services"]["cloud"]
        where = "/".join(parts)
        assert cloud["image"].startswith("rezztor/eagent-cloud:"), where
        assert cloud["profiles"] == ["cloud"], f"{where}: off unless asked for"
        assert "ports" not in cloud, f"{where}: it listens on a socket and publishes nothing"
        assert cloud["read_only"] is True and cloud["cap_drop"] == ["ALL"], where
        assert "no-new-privileges:true" in cloud["security_opt"], where
        assert cloud["pids_limit"] and cloud["mem_limit"], where
        assert all("nosuid" in entry for entry in cloud["tmpfs"]), where
        # No token, key, passphrase or database password has a default that opens anything.
        for name in ("REZZTOR_CLOUD_JOB_TOKEN", "REZZTOR_CLOUD_ADMIN_TOKEN", "REZZTOR_CLOUD_KEY",
                     "REZZTOR_CLOUD_STATE_PASSPHRASE", "REZZTOR_DB_CLOUD_PASSWORD"):
            assert name in cloud["environment"] and name + "_FILE" in cloud["environment"], f"{where}: {name}"
            assert ":?" not in cloud["environment"][name], f"{where}: {name} would stop the base stack"
        assert cloud["volumes"] == ["cloud_state:/var/lib/eagent-cloud", "cloud_socket:/run/cloud-socket"], where


def test_the_cloud_executor_has_its_own_networks_and_the_runner_has_neither():
    """`cloud_net` is the executor's way out to Azure; `cloud_db` is the executor's way to the state and has no gateway.
    The application is on neither (it reaches the executor by socket), and nor is the runner, which executes model code."""
    for parts in COMPOSE_FILES:
        compose = yaml.safe_load(read(*parts))
        where = "/".join(parts)
        assert _on(compose, "cloud_net") == {"cloud"}, where
        assert _on(compose, "cloud_db") == {"cloud", "postgres"}, where
        assert compose["networks"]["cloud_db"]["internal"] is True, f"{where}: the database path must have no gateway"
        assert not (compose["networks"].get("cloud_net") or {}).get("internal"), f"{where}: Azure must be reachable"
        assert compose["services"]["runner"]["networks"] == ["runner_net"], where
        assert "cloud" not in " ".join(compose["services"]["runner"].get("volumes") or []), where


def test_only_the_application_and_the_executor_share_the_cloud_socket_and_only_the_executor_holds_its_state():
    for parts in COMPOSE_FILES:
        compose = yaml.safe_load(read(*parts))
        where = "/".join(parts)
        assert _mounting(compose, "cloud_socket") == {"eagent", "cloud"}, where
        assert _mounting(compose, "cloud_state") == {"cloud"}, where
        cloud_mounts = " ".join(compose["services"]["cloud"]["volumes"])
        assert "docker.sock" not in cloud_mounts and "/eagent/usr" not in cloud_mounts and "runner_key" not in cloud_mounts


def test_the_application_is_given_the_tokens_and_the_database_role_password_but_not_the_executors_keys():
    for parts in COMPOSE_FILES:
        env = yaml.safe_load(read(*parts))["services"]["eagent"]["environment"]
        where = "/".join(parts)
        for name in ("REZZTOR_CLOUD_JOB_TOKEN", "REZZTOR_CLOUD_ADMIN_TOKEN", "REZZTOR_DB_CLOUD_PASSWORD"):
            assert name in env, f"{where}: {name}"
        # The passphrase and the sealing key belong to the executor. The application holding either would make the
        # database's ciphertext readable from the container that must not read it.
        assert not [name for name in env if name.startswith("REZZTOR_CLOUD_") and ("KEY" in name or "PASSPHRASE" in name)], where
        assert "cloud_socket:/run/cloud-socket" in yaml.safe_load(read(*parts))["services"]["eagent"]["volumes"], where


def test_the_cloud_image_is_not_built_with_a_download_at_run_time_or_a_second_provider_source():
    image = read("cloud_executor", "Dockerfile")
    final = image.split("\nFROM debian:stable-slim\n")[1]
    assert "curl" not in final and "wget" not in final and "pip install" in final
    assert "direct" not in "\n".join(
        line for line in read("cloud_executor", "cli.tfrc").splitlines() if not line.lstrip().startswith("#")
    )


def test_the_runner_can_not_resolve_azures_sign_in_and_management_hosts():
    """Agent code signed in to Azure from the runner once (a CLI and a device code). The application refuses
    that command by its text; under it, the names resolve to nowhere in both stacks."""
    needed = {"login.microsoftonline.com", "login.microsoft.com", "management.azure.com", "graph.microsoft.com"}
    for parts in (("docker", "run", "docker-compose.yml"), ("deploy", "customer", "docker-compose.yml")):
        runner = yaml.safe_load(read(*parts))["services"]["runner"]
        entries = [entry.split("=", 1) for entry in runner.get("extra_hosts", [])]
        for family in ("0.0.0.0", "[::]"):  # with only IPv4, the resolver still asked DNS for the IPv6 address
            assert needed <= {host for host, address in entries if address == family}, (parts, family)
        assert {address for _, address in entries} == {"0.0.0.0", "[::]"}, parts
        assert runner.get("read_only") is True, "a writable root would let a slot edit /etc/hosts"
