"""The customer installer, and the one hash that has to stay true.

Two files. `install.sh` is a POSIX bootstrap safe to pipe into a shell;
`installer.sh` is the bash installer that actually asks questions. Each holds
the hash of the next, so the single checksum Rezztor emails for `install.sh`
covers the installer, which covers the Compose file -- one hash for a customer
to check, and no others to forget.

That only works while the embedded hash matches the file actually shipped. If
it drifts, every fresh install fails at the same step with a message about
tampering, which is both alarming and wrong. Hence the first test here.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

CUSTOMER = Path(__file__).resolve().parents[1] / "deploy" / "customer"
INSTALLER = CUSTOMER / "installer.sh"
BOOTSTRAP = CUSTOMER / "install.sh"
COMPOSE = CUSTOMER / "docker-compose.yml"
GUARD = CUSTOMER / "rezztor-guard"

pytestmark = pytest.mark.skipif(
    not INSTALLER.is_file(), reason="customer bundle not present"
)


def script() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_the_embedded_compose_hash_is_current():
    """Change docker-compose.yml and you must change this hash in the same
    commit, or every install refuses the file it just downloaded."""
    match = re.search(r'COMPOSE_SHA256="([0-9a-f]{64})"', script())
    assert match, "install.sh has no COMPOSE_SHA256 to verify the download with"
    expected = hashlib.sha256(COMPOSE.read_bytes()).hexdigest()
    assert match.group(1) == expected, (
        "install.sh pins a stale docker-compose.yml hash.\n"
        f"  embedded {match.group(1)}\n"
        f"  actual   {expected}\n"
        "Update COMPOSE_SHA256 in deploy/customer/install.sh."
    )


def test_the_embedded_guard_hash_is_current():
    """The guard is fetched and verified exactly like the Compose file, so it
    drifts the same way: change rezztor-guard without this hash and every
    install stops at a message about tampering."""
    match = re.search(r'GUARD_SHA256="([0-9a-f]{64})"', script())
    assert match, "the installer has no GUARD_SHA256 to verify the download with"
    expected = hashlib.sha256(GUARD.read_bytes()).hexdigest()
    assert match.group(1) == expected, (
        "installer.sh pins a stale rezztor-guard hash.\n"
        f"  embedded {match.group(1)}\n"
        f"  actual   {expected}\n"
        "Update GUARD_SHA256 in deploy/customer/installer.sh."
    )


def test_the_guard_is_put_where_the_guide_says_to_install_it_from():
    """INSTALL.md step 6 installs `rezztor-guard` from the install directory.
    The publisher once staged the fence and never sent it, and the installer
    never asked for it, so the one boundary that is not inside the compose file
    was an instruction nobody could follow."""
    body = script()
    assert 'fetch_verified rezztor-guard "$GUARD_SHA256"' in body
    assert "chmod +x rezztor-guard" in body
    guide = (CUSTOMER / "INSTALL.md").read_text(encoding="utf-8")
    assert "sudo install -D -m 0755 rezztor-guard /opt/rezztor/rezztor-guard" in guide
    assert "sudo systemctl enable --now rezztor-guard rezztor-guard-runner" in guide
    unit = (CUSTOMER / "rezztor-guard.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/rezztor/rezztor-guard apply" in unit
    assert "ConditionPathExists=/opt/rezztor/rezztor-guard" in unit
    assert stat.S_IMODE(GUARD.stat().st_mode) & stat.S_IXUSR


def test_there_is_one_fence_and_it_is_loaded_before_docker():
    """The fence used to wait for Docker (`After=docker.service`), which left a
    short time at every boot with containers running and no fence. And two
    fences are two things to keep true: the old script must be gone everywhere."""
    unit = (CUSTOMER / "rezztor-guard.service").read_text(encoding="utf-8")
    directives = "\n".join(line for line in unit.splitlines() if not line.startswith("#"))
    assert "Before=docker.service" in directives
    assert "After=docker.service" not in directives and "Requires=docker.service" not in directives
    assert "ExecStop" not in directives, "stopping the unit must never remove the fence"
    repo = CUSTOMER.parent.parent
    assert not (CUSTOMER / "runner-firewall.sh").exists() and not (CUSTOMER / "rezztor-runner-firewall.service").exists()
    for path in ("scripts/build_offline_bundle.sh", "deploy/customer/installer.sh", "deploy/iso/install-target.sh",
                 "deploy/iso/host/rezztor-setup", "deploy/iso/test/test-iso.sh"):
        assert "runner-firewall" not in (repo / path).read_text(encoding="utf-8"), path


BLOCK_CHECK = """
RED=; OFF=; GREEN=; YELLOW=; DIM=; BOLD=; TEAL=; DEFAULT_NET_PREFIX=172.31
ip() { printf '%s\\n' "$ROUTES"; }
check_container_block
"""


def block_check(routes: str, prefix: str = "") -> subprocess.CompletedProcess:
    """Run the installer's own check against a routing table given as text."""
    body = script()
    functions = body[body.index("step()  {"):body.index("# ----", body.index("die()   {"))]
    functions += body[body.index("ip2int() {"):body.index("port_is_free() {")]
    env = {"PATH": "/usr/bin:/bin", "ROUTES": routes, **({"REZZTOR_NET_PREFIX": prefix} if prefix else {})}
    return subprocess.run(["bash", "-c", functions + BLOCK_CHECK], capture_output=True, text=True, env=env)


def test_the_installer_refuses_a_container_block_this_host_already_uses():
    """Docker creates a bridge on top of a customer's own network without a
    word. The check reads the routing table: a network this host is in, an
    address of its own, or any route as narrow as the block stops the install."""
    free = "default via 192.168.1.1 dev eth0\n192.168.1.0/24 dev eth0 proto kernel scope link src 192.168.1.20\nlocal 192.168.1.20 dev eth0 table local"
    done = block_check(free)
    assert done.returncode == 0 and "172.31.248.0/21 is free" in done.stdout, done.stdout + done.stderr

    for clash in ("172.31.250.0/24 dev br-abc proto kernel scope link src 172.31.250.1",   # an old runner network
                  "172.31.0.0/16 dev eth1 proto kernel scope link src 172.31.4.9",          # this host is in it
                  "local 172.31.249.7 dev eth1 table local proto kernel scope host",        # an address of its own
                  "172.31.252.0/22 via 192.168.1.1 dev eth0"):                              # a narrow route to it
        done = block_check(free + "\n" + clash)
        assert done.returncode == 1 and "REZZTOR_NET_PREFIX" in done.stderr, clash

    # Our own bridges are not a clash: a second run of the installer must pass.
    done = block_check(free + "\n172.31.250.0/24 dev rz-runner proto kernel scope link src 172.31.250.1")
    assert done.returncode == 0, done.stderr

    # The same host is fine with another block, and the choice is checked the same way.
    done = block_check(free + "\n172.31.0.0/16 dev eth1 proto kernel scope link src 172.31.4.9", prefix="10.99")
    assert done.returncode == 0 and "10.99.248.0/21 is free" in done.stdout
    assert block_check(free, prefix="10.99; reboot").returncode == 1


def test_a_wide_route_through_a_gateway_warns_and_does_not_stop_the_install():
    """`10.0.0.0/8 via the core switch` covers every block there is to choose.
    The bridges are more specific and still work, so this only says so."""
    done = block_check("default via 192.168.1.1 dev eth0\n172.16.0.0/12 via 192.168.1.1 dev eth0")
    assert done.returncode == 0, done.stderr
    assert "routes 172.16.0.0/12 elsewhere" in done.stdout


def test_the_installer_checks_the_docker_and_compose_versions_the_compose_file_needs():
    """`gw_priority` is refused outright by an older Compose. Found before the
    images are loaded, not after."""
    body = script()
    assert 'MIN_DOCKER="28.0.0"' in body and 'MIN_COMPOSE="2.33.1"' in body
    assert body.index("at_least \"${have#v}\" \"$MIN_COMPOSE\"") < body.index("# The check that actually bites")
    guide = (CUSTOMER / "INSTALL.md").read_text(encoding="utf-8")
    assert "28 or newer" in guide and "2.33.1" in guide
    assert "gw_priority: 1" in COMPOSE.read_text(encoding="utf-8")


def test_the_pinned_third_party_images_match_the_compose_file():
    """The installer names them so it can tell whether there is anything to
    pull. Named twice is drift waiting to happen, and the failure is a pull of
    an image the stack does not run."""
    compose = COMPOSE.read_text(encoding="utf-8")
    for variable in ("POSTGRES_IMAGE", "SEARXNG_IMAGE"):
        match = re.search(rf'{variable}="([^"]+)"', script())
        assert match, f"the installer no longer pins {variable}"
        assert f"image: {match.group(1)}" in compose, (
            f"{variable} is {match.group(1)}, which docker-compose.yml does not run"
        )






def test_it_is_executable():
    assert stat.S_IMODE(INSTALLER.stat().st_mode) & stat.S_IXUSR


def test_it_fails_fast():
    """Without `set -e`, a failed step is followed by the next one, and the
    installer would report success over a broken deployment."""
    assert "set -euo pipefail" in script()


class TestSecrets:


    def test_the_env_file_is_not_world_readable(self):
        """It holds the seed administrator password and the session key."""
        source = script()
        assert "chmod 600 .env" in source
        assert "chmod 600 runner_key" in source


class TestIrreversibleThings:
    def test_it_refuses_to_replace_an_existing_install(self):
        """FLASK_SECRET_KEY signs live sessions and the runner key is what the
        running runner has authorised. Regenerating either without asking
        would sign everyone out and stop code execution."""
        source = script()
        assert "check_not_already_installed" in source
        assert 'confirm "Start over anyway' in source

    def test_an_unattended_run_never_replaces_an_existing_install(self, tmp_path):
        """`confirm` answers yes to everything when nobody is there to ask, so a second `install.sh --unattended`
        (a retry, a configuration tool run twice) made a new runner key and new database passwords over a
        volume initialised with the old ones: every account and conversation locked away."""
        (tmp_path / ".env").write_text("FLASK_SECRET_KEY=the-one-in-use\n", encoding="utf-8")
        (tmp_path / "runner_key").write_text("the key the runner trusts\n", encoding="utf-8")
        ran = subprocess.run(
            ["bash", "-c", f"{_installer_functions(tmp_path)}\nUNATTENDED=1\ncheck_not_already_installed\nwrite_config"],
            cwd=tmp_path, env={"PATH": os.environ["PATH"], "REZZTOR_VERSION": "2026.09.08", "REZZTOR_PORT": "50080",
                               "REZZTOR_ADMIN_USER": "admin", "REZZTOR_ADMIN_PASSWORD": "a-password-of-the-test"},
            capture_output=True, text=True)
        assert ran.returncode != 0 and "existing installation" in ran.stdout + ran.stderr
        assert (tmp_path / ".env").read_text(encoding="utf-8") == "FLASK_SECRET_KEY=the-one-in-use\n"
        assert (tmp_path / "runner_key").read_text(encoding="utf-8") == "the key the runner trusts\n"




class TestVerification:
    def test_it_waits_for_the_health_endpoint(self):
        """`docker compose up` returning 0 means containers were created, not
        that the product runs."""
        source = script()
        assert "/api/health" in source
        assert "wait_until_healthy" in source

    def test_it_compares_the_running_version_to_the_requested_one(self):
        assert "Asked for" in script()

    def test_a_stopped_container_ends_the_wait_early(self):
        """Otherwise a container that dies on boot is a five-minute silence
        followed by a timeout that says nothing useful."""
        assert "The application container stopped" in script()


class TestFetching:
    def test_the_download_host_is_overridable(self):
        """An offline bundle and a staging host both need a different source,
        and neither should need a different script."""
        assert "REZZTOR_INSTALL_URL" in script()

    def test_a_local_compose_file_is_used_as_is(self):
        """Installing from the offline bundle must not require the network."""
        assert 'if [[ -f docker-compose.yml ]]' in script()

    def test_the_docstring_tells_the_reader_to_read_it(self):
        """It is meant to be downloaded and inspected, not piped into a shell,
        and the checksum that matters arrives by email rather than from the
        same host as the script."""
        source = script()
        assert "Read it before you run it" in source
        assert "sha256sum install.sh" in source


class TestUnattended:
    def test_every_prompt_has_an_environment_override(self):
        """Which is what makes --unattended work without a second code path."""
        source = script()
        for var in (
            "REZZTOR_VERSION",
            "REZZTOR_PORT",
            "REZZTOR_ADMIN_USER",
            "REZZTOR_ADMIN_PASSWORD",
        ):
            assert f"ask {var} " in source or f"ask_secret {var} " in source, var

    def test_unattended_refuses_rather_than_inventing_a_secret(self):
        assert 'die "--unattended, but $__var is unset"' in script()


class TestDockerHandling:
    """`docker info` failing has four causes and they need four answers.

    Reported by a user who hit "The Docker daemon is not reachable. Is it
    running, and is this user in the docker group?" -- one message covering
    every case, which sends someone to restart a service when their real
    problem is a group membership that has not taken effect yet.
    """

    def test_permission_denied_is_told_apart_from_a_stopped_daemon(self):
        source = script()
        assert '*"permission denied"*' in source
        assert '*"Cannot connect to the Docker daemon"*' in source

    def test_the_group_fix_is_offered(self):
        """The trap: `usermod -aG` does nothing for a session that has already
        started, so re-running in the same terminal fails forever.

        This asserted the exact words "NEW terminal" until that advice turned
        out to be wrong -- a terminal opened from the same login session
        inherits its groups, so it can fail too. See TestStaleSessionGroups
        for what replaced it."""
        source = script()
        assert "usermod -aG docker" in source
        assert "newgrp docker" in source

    def test_an_unrecognised_error_is_shown_rather_than_guessed_at(self):
        assert 'Docker is installed but did not answer' in script()

    def test_installing_docker_is_offered_never_automatic(self):
        """It runs a third-party script as root. Nobody should discover that
        after the fact."""
        source = script()
        assert 'confirm "Install Docker now?"' in source
        assert "get.docker.com" in source
        # The command is printed before it is offered.
        assert source.index("curl -fsSL https://get.docker.com | sudo sh") < source.index(
            'confirm "Install Docker now?"'
        )

    def test_declining_the_install_stops_rather_than_continuing(self):
        assert 'die "Install Docker, then run this again."' in script()

    def test_it_points_at_the_official_docs_for_unsupported_systems(self):
        assert "https://docs.docker.com/engine/install/" in script()

    def test_it_stops_after_installing_when_the_group_is_not_yet_active(self):
        """Carrying on would fail at the first docker command with a
        permission error, immediately after reporting success."""
        source = script()
        assert "Open a new terminal" in source
        assert "exit 0" in source

    def test_starting_a_stopped_daemon_is_also_offered_not_assumed(self):
        assert 'confirm "Start it now' in script()



class TestBootstrap:
    """`install.sh` is the file people pipe into a shell, so it has to survive
    being run by dash and must never ask a question.

    An interactive script cannot be piped at all: stdin *is* the script, so a
    `read` consumes the next line of source rather than the user's answer and
    execution resumes past it, silently. That is why this file exists.
    """

    def boot(self) -> str:
        return BOOTSTRAP.read_text(encoding="utf-8")

    def test_it_is_posix_sh(self):
        assert self.boot().startswith("#!/bin/sh")

    def test_it_parses_under_a_posix_shell(self):
        """`sh -n` on this machine, and the absence of bashisms for the dash
        that Debian and Ubuntu customers actually have."""
        import subprocess

        assert subprocess.run(["sh", "-n", str(BOOTSTRAP)]).returncode == 0

        # Comments stripped first. The header explains what dash lacks, and
        # naming `[[ ]]` in prose is not the same as using it -- a check that
        # cannot tell those apart fails on the documentation that exists to
        # prevent the bug.
        code = "\n".join(
            line for line in self.boot().splitlines() if not line.lstrip().startswith("#")
        )
        for bashism in ("[[", "printf -v", "declare ", "local ", "=()"):
            assert bashism not in code, f"the bootstrap uses {bashism!r}, which dash lacks"

    def test_it_asks_nothing(self):
        """The whole reason it is separate from the installer."""
        source = self.boot()
        assert "read " not in source
        assert "read -" not in source

    def test_it_hands_the_installer_a_terminal(self):
        """Piped, stdin is the pipe. The installer needs a keyboard."""
        assert "/dev/tty" in self.boot()

    def test_it_runs_the_installer_under_bash(self):
        assert "exec bash" in self.boot()

    def test_it_verifies_what_it_downloads(self):
        source = self.boot()
        assert "INSTALLER_SHA256" in source
        assert "sha256sum" in source

    def test_the_hash_is_a_placeholder_in_the_repository(self):
        """Stamped at publish time from the file actually published, so it
        cannot drift from the installer it names."""
        assert "__INSTALLER_SHA__" in self.boot()

    def test_an_unstamped_bootstrap_skips_verification_rather_than_failing(self):
        """Running it straight out of a git checkout must not die on a
        placeholder that was never meant to be a hash."""
        assert 'if [ "$INSTALLER_SHA256" != "__INSTALLER_SHA__" ]' in self.boot()

    def test_a_local_installer_is_preferred_over_the_network(self):
        """The offline bundle ships both files and has no network."""
        assert "[ -f ./installer.sh ]" in self.boot()

    def test_it_explains_why_it_exists(self):
        source = self.boot()
        assert "stdin *is* the script" in source
        assert "dash" in source


class TestStaleSessionGroups:
    """The permission-denied error covers two problems with opposite fixes.

    Reported twice from real runs. `docker info` says only "permission
    denied"; whether the account lacks the group, or merely this session
    does, is the difference between running `usermod` and running `newgrp`.
    The script asks the system rather than guessing: `id -nG` is what the
    session holds, `id -nG "$USER"` is what the group database says.
    """

    def test_it_compares_session_groups_with_configured_groups(self):
        source = script()
        assert "in_group_session" in source
        assert "in_group_config" in source
        assert 'id -nG "$USER"' in source

    def test_a_stale_session_is_named_as_such(self):
        assert "this session is older than that change" in script()

    def test_it_says_a_new_terminal_may_not_be_enough(self):
        """A terminal opened from the same login session inherits that
        session's groups, so the usual advice quietly fails."""
        source = script()
        assert "open a new" in source and "terminal" in source
        assert "log out" in source
        assert "desktop session, not just this terminal" in source

    def test_it_offers_to_continue_rather_than_only_complaining(self):
        """`sg docker` starts a shell that does hold the group, so the
        install can proceed without the user going away and coming back."""
        source = script()
        assert "sg docker" in source
        assert 'confirm "Carry on in a shell that does have the group?"' in source

    def test_the_reexec_cannot_loop(self):
        source = script()
        assert "REZZTOR_GROUP_REEXEC" in source
        assert '[[ -z "${REZZTOR_GROUP_REEXEC:-}" ]]' in source

    def test_not_being_in_the_group_at_all_is_a_different_message(self):
        assert "the docker group is not on this account at all" in script()




def test_every_variable_the_compose_file_demands_is_offered_to_the_customer():
    """A `:?` variable stops the stack dead if it is unset.

    So every one of them has to reach the customer somehow -- generated by the
    installer, or listed in .env.example for them to fill in. Adding the SearXNG
    secret to the compose file without doing either is exactly how a fresh
    install breaks on a variable nobody mentioned, and it is the kind of gap
    that only shows up on someone else's machine.
    """
    import re

    compose = (CUSTOMER / "docker-compose.yml").read_text(encoding="utf-8")
    example = (CUSTOMER / ".env.example").read_text(encoding="utf-8")
    installer = script()

    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose))
    assert required, "no required variables found -- the pattern stopped matching"

    for name in sorted(required):
        offered = re.search(rf"^{name}=", example, re.M) or re.search(
            rf"^{name}=", installer, re.M
        )
        assert offered, (
            f"{name} is required by docker-compose.yml but the customer is never "
            "given it: not generated by the installer, not in .env.example"
        )


def test_the_published_package_carries_the_network_guard():
    """The one boundary that is not inside docker-compose.yml.

    Every other guarantee in this package applies itself when the stack starts.
    This one is a firewall table on the customer's host, because Docker cannot
    express "the internet but not your network" -- so it has to be shipped and
    loaded, and a package that quietly stopped carrying it would leave
    installations that look complete and are not. INSTALL.md tells them to
    install it; this asserts they were given it.
    """
    repo = CUSTOMER.parent.parent
    bundle = (repo / "scripts" / "build_offline_bundle.sh").read_text(encoding="utf-8")
    assert 'cp deploy/customer/rezztor-guard "$BUNDLE/"' in bundle, (
        "the delivery bundle does not carry the network guard"
    )
    assert 'cp deploy/customer/rezztor-guard.service "$BUNDLE/"' in bundle, (
        "the unit that loads the guard before Docker is not shipped"
    )
    assert 'cp deploy/customer/rezztor-guard-runner.service "$BUNDLE/"' in bundle, (
        "the unit that keeps the runner's accounts off its loopback is not shipped"
    )

    guide = (CUSTOMER / "INSTALL.md").read_text(encoding="utf-8")
    assert "rezztor-guard" in guide, "the guide never tells anyone to load it"
    assert "python3" in guide and "nftables" in guide, "the guide does not say what the guard needs on the host"


CLOUD_SECRETS = (
    "REZZTOR_CLOUD_JOB_TOKEN", "REZZTOR_CLOUD_ADMIN_TOKEN", "REZZTOR_CLOUD_KEY", "REZZTOR_CLOUD_STATE_PASSPHRASE",
    "REZZTOR_DB_CLOUD_PASSWORD",
)


def test_the_installer_generates_every_secret_the_cloud_executor_needs_and_none_twice():
    """Five secrets, each from its own call: the two tokens must differ or the executor refuses its administration
    plane, and a passphrase generated at build time would be one every customer shares."""
    body = script()
    generated = []
    for name in CLOUD_SECRETS:
        match = re.search(rf"^{name}=\$\((openssl rand [^)]+)\)$", body, re.M)
        assert match, f"the installer does not generate {name}"
        generated.append(match.group(1))
    assert len(generated) == 5 and generated.count("openssl rand -hex 32") == 3
    assert "openssl rand -base64 32" in generated, "REZZTOR_CLOUD_KEY is 32 bytes, base64"
    example = (CUSTOMER / ".env.example").read_text(encoding="utf-8")
    for name in CLOUD_SECRETS:
        assert re.search(rf"^{name}=$", example, re.M), f"{name} is not listed in .env.example"


ANSIBLE_SECRETS = ("REZZTOR_ANSIBLE_EXECUTOR_TOKEN", "REZZTOR_ANSIBLE_ADMIN_TOKEN", "REZZTOR_ANSIBLE_CREDENTIAL_KEY")


def _installer_functions(folder: Path) -> str:
    """A shell line that loads the installer without its last line, so a test can call one function of it."""
    body, call = script().rsplit("\n", 2)[0], script().rstrip().rsplit("\n", 1)[1]
    assert call == 'main "$@"'
    (folder / "functions.sh").write_text(body + "\n", encoding="utf-8")   # a file: the script reads its own path
    return f". {folder / 'functions.sh'}"


def _summary(tmp_path: Path, hostname: str) -> subprocess.CompletedProcess:
    """The installer's last words, on a host whose `hostname` command is the given script."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "hostname").write_text(f"#!/bin/sh\n{hostname}\n", encoding="utf-8")
    (tools / "hostname").chmod(0o755)
    return subprocess.run(
        ["bash", "-c", f"{_installer_functions(tmp_path)}\nsummary"], cwd=tmp_path, capture_output=True, text=True,
        env={"PATH": f"{tools}:{os.environ['PATH']}", "REZZTOR_PORT": "50080", "REZZTOR_ADMIN_USER": "admin",
             "REZZTOR_ADMIN_PASSWORD": "a-password-of-the-test"})


def test_the_summary_is_printed_on_a_host_whose_hostname_command_has_no_dash_i(tmp_path):
    """`hostname -I` is the Debian and Red Hat one. Elsewhere it is a usage error, and under `set -e` with
    `pipefail` that ended the installer without a word, at the one place it shows the password it generated."""
    ran = _summary(tmp_path, "echo 'usage: hostname [-f]' >&2; exit 64")
    assert ran.returncode == 0 and "a-password-of-the-test" in ran.stdout
    assert "http://localhost:50080/app" in ran.stdout


def test_the_installer_ends_by_saying_the_network_guard_is_still_to_be_loaded(tmp_path):
    """The guide calls the guard the one step that does not apply itself ("do this before anyone uses it"). The
    installer ended with "is running" and three next steps, none of them the guard, so an installation made with
    the installer alone had a runner that could reach the management network and nothing said so."""
    said = _summary(tmp_path, "echo 192.0.2.10").stdout
    said = said[said.index("Next"):said.index("Keep")]
    assert "1. Load the network guard" in said and "INSTALL.md" in said and "before anyone uses it" in said


def test_the_guides_manual_steps_write_every_variable_compose_refuses_to_start_without():
    """The guide's own `.env` (section 4, for somebody who does not run the installer) had five variables; the
    Compose file stops on three more ("set a database password"), so following the guide ended in an error."""
    compose = (CUSTOMER / "docker-compose.yml").read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
    guide = (CUSTOMER / "INSTALL.md").read_text(encoding="utf-8")
    written = re.search(r"cat > \.env <<EOF\n(.*?)\nEOF\n", guide, re.S).group(1)
    assert required and required <= {line.split("=", 1)[0] for line in written.splitlines()}, \
        f"the guide's .env lacks {sorted(required - {line.split('=', 1)[0] for line in written.splitlines()})}"


def _write_config(tmp_path: Path, user: str, password: str) -> subprocess.CompletedProcess:
    where = tmp_path / "install"
    where.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", f"{_installer_functions(tmp_path)}\ncheck_profiles\nwrite_config"], cwd=where,
        env={"PATH": os.environ["PATH"], "REZZTOR_VERSION": "2026.09.08", "REZZTOR_PORT": "50080",
             "REZZTOR_ADMIN_USER": user, "REZZTOR_ADMIN_PASSWORD": password},
        capture_output=True, text=True)


def test_the_administrators_password_reaches_the_product_as_it_was_typed(tmp_path):
    """Compose reads `.env`, not a shell: in an unquoted value `$name` is a variable and ` #` starts a comment, so
    `Hunter2$ecret #1` was stored as `Hunter2` and the administrator was left with a password nobody chose.
    Inside single quotes Compose takes every character as it is."""
    password = 'Hunter2$ecret #1"x\\y ${HOME}'
    assert _write_config(tmp_path, "admin", password).returncode == 0
    lines = (tmp_path / "install" / ".env").read_text(encoding="utf-8").splitlines()
    assert f"REZZTOR_ADMIN_PASSWORD='{password}'" in lines and "REZZTOR_ADMIN_USER='admin'" in lines


@pytest.mark.parametrize("user,password", [("admin", "it's-a-long-password"), ("ad'min", "a-password-of-the-test"),
                                           ("admin", "two\nlines-of-a-password")])
def test_a_name_or_password_the_file_cannot_carry_is_refused_before_anything_is_written(tmp_path, user, password):
    ran = _write_config(tmp_path, user, password)
    assert ran.returncode != 0 and "single quote" in ran.stderr
    assert not (tmp_path / "install" / ".env").exists() and not (tmp_path / "install" / "runner_key").exists()


def test_the_installer_makes_the_secrets_of_network_automation_so_nobody_pastes_a_token(tmp_path):
    """The first machine installed from the ISO asked for two tokens to be pasted: the installer wrote the Azure
    secrets and none of these three. Each comes from its own call, because the executor refuses its
    administration plane when the two tokens are equal. They are written only with the executor: the application
    switches the plugin on when it is given a job token, and a token alone would switch on a plugin that cannot work."""
    body = script()
    made = {name: re.search(rf"^{name}=\$\((openssl rand [^)]+)\)$", body, re.M) for name in ANSIBLE_SECRETS}
    assert all(made.values()), f"the installer does not generate {[n for n, m in made.items() if not m]}"
    assert [made[name].group(1) for name in ANSIBLE_SECRETS] == ["openssl rand -hex 32"] * 2 + ["openssl rand -base64 32"]
    example = (CUSTOMER / ".env.example").read_text(encoding="utf-8")
    for name in ANSIBLE_SECRETS:
        assert re.search(rf"^{name}=$", example, re.M), f"{name} is not listed in .env.example"

    # Run for real: the file it writes, with and without an executor asked for.
    def written(profiles: str | None) -> dict[str, str]:
        where = tmp_path / (profiles or "none")
        where.mkdir()
        env = {"PATH": os.environ["PATH"], "REZZTOR_VERSION": "2026.09.08", "REZZTOR_PORT": "50080",
               "REZZTOR_ADMIN_USER": "admin", "REZZTOR_ADMIN_PASSWORD": "a-password-of-the-test"}
        if profiles is not None:
            env["COMPOSE_PROFILES"] = profiles
        subprocess.run(["bash", "-c", f"{_installer_functions(tmp_path)}\ncheck_profiles\nwrite_config >/dev/null"],
                       cwd=where, env=env, check=True, capture_output=True, text=True)
        lines = (where / ".env").read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))

    plain, cloud, asked, both = written(None), written("cloud"), written("ansible"), written("ansible,cloud")
    heredocs = re.findall(r"cat >>? \.env <<EOF\n(.*?)\nEOF\n", body, re.S)
    assert len(heredocs) == 2 and not any("`" in text for text in heredocs), \
        "a backtick in the file's comments is run as a command (it printed 'cloud: command not found')"
    assert "COMPOSE_PROFILES" not in plain, "a hand install starts no executor it was not asked for"
    assert asked["COMPOSE_PROFILES"] == "ansible", "in the file, so a later `docker compose up` starts it too"
    assert both["COMPOSE_PROFILES"] == "ansible,cloud"
    for env in (plain, cloud):
        assert not [name for name in env if name.startswith("REZZTOR_ANSIBLE_")], "no token without the executor"
    for env in (asked, both):
        assert re.fullmatch(r"[0-9a-f]{64}", env["REZZTOR_ANSIBLE_EXECUTOR_TOKEN"])
        assert re.fullmatch(r"[0-9a-f]{64}", env["REZZTOR_ANSIBLE_ADMIN_TOKEN"])
        assert env["REZZTOR_ANSIBLE_EXECUTOR_TOKEN"] != env["REZZTOR_ANSIBLE_ADMIN_TOKEN"]
        assert len(base64.b64decode(env["REZZTOR_ANSIBLE_CREDENTIAL_KEY"])) == 32
    assert both["REZZTOR_ANSIBLE_ADMIN_TOKEN"] != asked["REZZTOR_ANSIBLE_ADMIN_TOKEN"], "made on each machine"


def test_an_executor_that_was_asked_for_is_part_of_the_stack_and_an_unknown_one_is_refused(tmp_path):
    def run(profiles: str, call: str) -> subprocess.CompletedProcess:
        fake = 'docker() { printf "%s\\n" "$@" >&3; }'   # images_present hides docker's own output
        return subprocess.run(["bash", "-c", f"{_installer_functions(tmp_path)}\n{fake}\n{call}"], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "COMPOSE_PROFILES": profiles, "REZZTOR_VERSION": "2026.09.08"})

    asked = run("ansible,cloud", "images_present 3>&1").stdout.split()
    assert {"rezztor/eagent:2026.09.08", "rezztor/eagent-ansible:2026.09.08", "rezztor/eagent-cloud:2026.09.08"} <= set(asked)
    assert "rezztor/eagent-ansible:2026.09.08" not in run("", "images_present 3>&1").stdout
    assert run("ansible", "check_profiles").returncode == 0
    refused = run("ansible,everything", "check_profiles")
    assert refused.returncode != 0 and "ansible, cloud, or both" in refused.stderr + refused.stdout


def test_every_cloud_variable_the_compose_file_reads_is_one_the_customer_can_set():
    compose = COMPOSE.read_text(encoding="utf-8")
    example = (CUSTOMER / ".env.example").read_text(encoding="utf-8")
    for name in sorted(set(re.findall(r"\$\{(REZZTOR_(?:CLOUD|DB_CLOUD)_[A-Z_]+)[-:}]", compose))):
        assert name.removesuffix("_FILE") in example, f"{name} is read by the compose file and not documented"


def test_the_guide_and_the_bundle_cover_the_cloud_executor():
    guide = (CUSTOMER / "INSTALL.md").read_text(encoding="utf-8")
    assert "--profile cloud" in guide and "REZZTOR_CLOUD_STATE_PASSPHRASE" in guide and "management.azure.com" in guide
    assert "never deletes" in guide.lower().replace("\n", " ")
    bundle = (CUSTOMER.parent.parent / "scripts" / "build_offline_bundle.sh").read_text(encoding="utf-8")
    assert 'CLOUD="$REGISTRY/eagent-cloud:$VERSION"' in bundle and 'docker save "$CLOUD"' in bundle
    assert '"$CLOUD"' in bundle.split("for image in")[1].split("; do")[0], "the bundle must refuse to build without the image"

