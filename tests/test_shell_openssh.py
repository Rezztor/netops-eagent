"""End-to-end tests for the OpenSSH-backed interactive shell.

These run against a real `sshd`, started on a loopback port with throwaway keys,
because the thing worth testing is the handshake with a genuine SSH server. A
mocked transport would pass while the argument construction, the PTY handshake
and the host key policy -- the three things this module actually does -- were all
wrong.

Skipped when `sshd` is unavailable rather than mocked around it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from plugins._code_execution.helpers.shell_openssh import OpenSSHInteractiveSession

SSHD = shutil.which("sshd") or "/usr/bin/sshd"
pytestmark = pytest.mark.skipif(
    not Path(SSHD).exists() or not shutil.which("ssh"),
    reason="needs a real sshd and ssh client",
)


class _Logger:
    """Stands in for helpers.log.Log, which needs an AgentContext to build."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def log(self, type: str = "", content: str = "", **_: object) -> None:
        self.entries.append((type, content))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def sshd_server(tmp_path_factory):
    """A loopback sshd authenticating the current user by key.

    Key auth, not password: sshd cannot verify a password without root, and key
    auth is what a runner pod should use anyway.
    """
    root = tmp_path_factory.mktemp("sshd")
    port = _free_port()

    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(root / "hostkey"), "-N", ""],
        check=True,
    )
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(root / "id"), "-N", ""],
        check=True,
    )
    (root / "authorized_keys").write_bytes((root / "id.pub").read_bytes())
    for name in ("id", "authorized_keys"):
        os.chmod(root / name, 0o600)

    (root / "sshd_config").write_text(
        f"""Port {port}
ListenAddress 127.0.0.1
HostKey {root}/hostkey
PidFile {root}/sshd.pid
AuthorizedKeysFile {root}/authorized_keys
PasswordAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes no
Subsystem sftp internal-sftp
"""
    )

    proc = subprocess.Popen(
        [SSHD, "-f", str(root / "sshd_config"), "-D", "-E", str(root / "sshd.log")]
    )
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        proc.terminate()
        pytest.skip("sshd did not start")

    yield {"port": port, "root": root, "user": os.environ.get("USER") or "root"}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _session(sshd_server, **overrides) -> OpenSSHInteractiveSession:
    root = sshd_server["root"]
    kwargs = {
        "identity_file": str(root / "id"),
        "known_hosts_file": str(root / "known_hosts"),
        "strict_host_key_checking": "accept-new",
    }
    kwargs.update(overrides)
    return OpenSSHInteractiveSession(
        _Logger(),  # type: ignore[arg-type]
        "127.0.0.1",
        sshd_server["port"],
        sshd_server["user"],
        "",
        **kwargs,
    )


async def _run(session: OpenSSHInteractiveSession, command: str, seconds: float = 12):
    """Send a command and collect output until it stops arriving."""
    await session.send_command(command)
    deadline = time.monotonic() + seconds
    full = ""
    quiet = 0
    while time.monotonic() < deadline:
        full, partial = await session.read_output()
        if not partial:
            quiet += 1
            if quiet >= 3 and full:
                break
        else:
            quiet = 0
    return full


def test_it_connects_and_runs_a_command(sshd_server):
    async def scenario():
        session = _session(sshd_server)
        await session.connect(keepalive_interval=5)
        try:
            output = await _run(session, "echo hello-from-the-runner")
            assert "hello-from-the-runner" in output
        finally:
            await session.close()

    asyncio.run(scenario())


def test_the_shell_is_stateful_across_commands(sshd_server):
    """An interactive shell, not a series of one-shot execs. The agent relies on
    this: it sets a variable in one call and reads it in the next."""

    async def scenario():
        session = _session(sshd_server)
        await session.connect()
        try:
            await _run(session, "MARKER=persisted-value")
            output = await _run(session, "echo $MARKER")
            assert "persisted-value" in output
        finally:
            await session.close()

    asyncio.run(scenario())


def test_the_working_directory_is_honoured(sshd_server, tmp_path):
    async def scenario():
        target = tmp_path / "workdir"
        target.mkdir()
        session = _session(sshd_server, cwd=str(target))
        await session.connect()
        try:
            output = await _run(session, "pwd")
            assert str(target) in output
        finally:
            await session.close()

    asyncio.run(scenario())


def test_output_is_cleaned_of_terminal_control_sequences(sshd_server):
    """A PTY means the remote end emits ANSI. It must not reach the model as
    literal escape codes -- the same guarantee the paramiko path gives."""

    async def scenario():
        session = _session(sshd_server)
        await session.connect()
        try:
            output = await _run(session, r"printf '\033[31mRED\033[0m\n'")
            assert "RED" in output
            assert "\x1b[" not in output
        finally:
            await session.close()

    asyncio.run(scenario())


def test_a_terminated_session_reports_itself_terminated(sshd_server):
    async def scenario():
        session = _session(sshd_server)
        await session.connect()
        assert session.is_terminated() is False
        await session.close()
        assert session.is_terminated() is True

    asyncio.run(scenario())


def test_a_changed_host_key_is_refused(sshd_server, tmp_path):
    """The reason this prototype exists, alongside the licence question.

    paramiko's AutoAddPolicy with no known_hosts accepts every key on every
    connection, so a substituted server is indistinguishable from the real one.
    Here the key is pinned on first use and a different key is a hard failure.
    """

    async def scenario():
        known_hosts = tmp_path / "known_hosts"

        first = _session(sshd_server, known_hosts_file=str(known_hosts))
        await first.connect()
        await first.close()
        assert known_hosts.exists() and known_hosts.stat().st_size > 0

        # Substitute a different key for the same host:port.
        forged = tmp_path / "forged"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(forged), "-N", ""],
            check=True,
        )
        entry = known_hosts.read_text().split()
        known_hosts.write_text(
            f"{entry[0]} {(forged.with_suffix('.pub')).read_text().strip()}\n"
        )

        second = _session(
            sshd_server,
            known_hosts_file=str(known_hosts),
            strict_host_key_checking="yes",
        )
        with pytest.raises(Exception) as caught:
            await second.connect(keepalive_interval=0)
        await second.close()
        assert "host key" in str(caught.value).lower()

    asyncio.run(scenario())


def test_strict_checking_refuses_an_unknown_host(sshd_server, tmp_path):
    """With verification set to strict and nothing on record, the connection
    must fail rather than trusting the first key it is offered."""

    async def scenario():
        session = _session(
            sshd_server,
            known_hosts_file=str(tmp_path / "empty_known_hosts"),
            strict_host_key_checking="yes",
        )
        with pytest.raises(Exception):
            await session.connect(keepalive_interval=0)
        await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "hostile",
    [
        "host; touch /tmp/eagent-pwned",
        "host$(touch /tmp/eagent-pwned)",
        "host`touch /tmp/eagent-pwned`",
        "host && touch /tmp/eagent-pwned",
        "host\ntouch /tmp/eagent-pwned",
    ],
)
def test_the_command_line_cannot_be_broken_out_of(hostile):
    """TTYSession runs its command through /bin/sh -c, which paramiko has no
    equivalent of. That is a hazard this approach introduces, so the quoting is
    asserted directly rather than assumed."""
    session = OpenSSHInteractiveSession(
        _Logger(), hostile, 22, hostile, "", identity_file="/dev/null"  # type: ignore[arg-type]
    )
    argv = session._ssh_argv(5)

    # The dangerous text survives as data...
    assert hostile.split()[0] in argv or "host" in argv
    # ...but none of the metacharacters are live.
    import shlex

    parsed = shlex.split(argv)
    assert "touch" not in parsed, f"shell metacharacters escaped quoting: {argv}"
    assert parsed[0] == "ssh"


def test_key_authentication_never_offers_a_password():
    """With an identity file, ssh must not fall back to asking for one -- a
    prompt that hangs a non-interactive runner until it times out."""
    session = OpenSSHInteractiveSession(
        _Logger(), "runner", 22, "agent", "hunter2", identity_file="/keys/id"  # type: ignore[arg-type]
    )
    argv = session._ssh_argv(5)
    assert "PreferredAuthentications=publickey" in argv
    assert "BatchMode=yes" in argv
    assert "hunter2" not in argv, "the password reached the command line"


def test_the_password_is_never_placed_on_the_command_line():
    """It is typed into the PTY in response to a prompt. Anything else puts a
    credential in /proc/<pid>/cmdline, readable by every process on the host."""
    session = OpenSSHInteractiveSession(
        _Logger(), "runner", 22, "agent", "hunter2"  # type: ignore[arg-type]
    )
    assert "hunter2" not in session._ssh_argv(5)


def test_keepalive_is_translated_to_ssh_options():
    """paramiko's transport.set_keepalive has a direct equivalent; a runner pod
    behind a load balancer needs it or idle sessions are silently dropped."""
    session = OpenSSHInteractiveSession(_Logger(), "runner", 22, "agent", "")  # type: ignore[arg-type]
    assert "ServerAliveInterval=7" in session._ssh_argv(7)
    assert "ServerAliveInterval" not in session._ssh_argv(0)


