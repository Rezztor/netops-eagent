"""An interactive remote shell driven by the OpenSSH client, not by a library.

The only remote execution transport. It began as an alternative to a paramiko
session in `shell_ssh.py`; that module was removed once this had replaced it
everywhere, and the product no longer depends on paramiko at all
(ADR 0018).

Why this exists
---------------

Three reasons, in ascending order of how much they matter:

1. paramiko was the one LGPL dependency in an otherwise permissive tree. The
   obligations were satisfiable -- and were satisfied while it shipped -- but
   spawning a separate executable removes the question rather than answering
   it. OpenSSH is BSD-licensed, and a subprocess is not linking under any
   reading.

2. The paramiko session disabled host key verification outright:
   `set_missing_host_key_policy(AutoAddPolicy())` with no known_hosts loaded,
   so every key was unknown and every key was accepted. That is survivable
   while the target is localhost. Pointed at a runner pod across the cluster
   network, it means the channel that executes arbitrary code can be
   intercepted without anything noticing. Here, verification is on by default.

3. The hard part -- PTY allocation, streaming, idle detection, exit codes --
   already exists in `tty_session.TTYSession` and is already used by the local
   execution path. This module is mostly argument construction and an
   authentication handshake; the transport is a process.

What it costs
-------------

Two things, both real:

- The command is assembled into a string and run through `/bin/sh -c`, because
  that is what `TTYSession` does. Every argument is therefore passed through
  `shlex.quote`. A library call has no shell to escape for, so this is a hazard
  this approach introduces and paramiko does not have. `_ssh_argv` is the only
  place that builds the command, and it quotes at the boundary.

- Password authentication is answered by watching the PTY for a prompt, the way
  a person would. It works, and it avoids a dependency on `sshpass` (which is
  GPL and awkward to ship), but it is pattern matching against human-readable
  output. Key authentication has no such weakness and is what a runner pod
  should use.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
import uuid
from typing import Tuple

from helpers.log import Log
from helpers.print_style import PrintStyle
from plugins._code_execution.helpers.shell_text import (
    PAGER_DISABLE_COMMAND,
    clean_string,
)
from plugins._code_execution.helpers.tty_session import TTYSession

# OpenSSH asks for a password on the terminal. These are the forms it uses;
# matched case-insensitively against recent output only, so a password prompt
# printed *by a command the agent ran* cannot be mistaken for the login one --
# by then `_authenticated` is set and this is not consulted.
_PASSWORD_PROMPT = re.compile(rb"(?:password|passphrase)[^:]*:\s*$", re.I)

# Host key changes and other refusals. Detected so the failure is reported as
# what it is instead of timing out with an empty buffer.
_HOST_KEY_FAILURE = re.compile(
    rb"REMOTE HOST IDENTIFICATION HAS CHANGED|"
    rb"Host key verification failed|"
    rb"no matching host key type",
    re.I,
)
_AUTH_FAILURE = re.compile(
    rb"Permission denied|Too many authentication failures|"
    rb"Connection refused|Connection closed by|Could not resolve hostname",
    re.I,
)

# How long to wait for each read while handshaking. Short enough that a prompt
# is noticed promptly, long enough not to spin.
_POLL_SECONDS = 0.1


def cwd_preamble(cwd: str, failure_echo: str) -> str:
    """The shell prefix that puts a session in its working directory.

    Both halves matter. `mkdir -p` because the only process that could create
    the directory otherwise runs in the application container, against a
    different filesystem. And a guarded failure because the original was
    `cd {cwd}; ...` -- a plain semicolon, so a directory that could not be
    entered left the shell running in the login home with nothing said, and on
    a shared runner that home is the same one for every account.
    """
    quoted = shlex.quote(cwd)
    return f"{{ mkdir -p {quoted} && cd {quoted}; }} || echo {failure_echo}; "


class OpenSSHInteractiveSession:
    """Drop-in replacement for `SSHInteractiveSession`.

    The constructor signature is deliberately identical, including the unused
    positional shape, so the two can be swapped without touching the caller.
    """

    def __init__(
        self,
        logger: Log,
        hostname: str,
        port: int,
        username: str,
        password: str,
        cwd: str | None = None,
        *,
        identity_file: str | None = None,
        known_hosts_file: str | None = None,
        strict_host_key_checking: str | None = None,
    ):
        self.logger = logger
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.cwd = cwd

        # Verification policy. "accept-new" trusts a host the first time and
        # refuses it if the key ever changes -- weaker than pre-provisioning a
        # key, far stronger than paramiko's AutoAddPolicy, which accepts every
        # key every time because no known_hosts is ever loaded. A runner pod
        # with a key baked into its image should use "yes".
        self.identity_file = identity_file or os.getenv("REZZTOR_RUNNER_IDENTITY_FILE")
        self.known_hosts_file = known_hosts_file or os.getenv(
            "REZZTOR_RUNNER_KNOWN_HOSTS"
        )
        self.strict_host_key_checking = (
            strict_host_key_checking
            or os.getenv("REZZTOR_RUNNER_HOST_KEY_CHECKING")
            or "accept-new"
        )

        self._session: TTYSession | None = None
        self._authenticated = False
        self.full_output = b""
        self.last_command = b""
        self.trimmed_command_length = 0
        self._exit_code: int | None = None

    # ── command construction ──────────────────────────────────────────

    def _ssh_argv(self, keepalive_interval: int) -> str:
        """Build the ssh command line.

        The single place a shell string is produced, and therefore the single
        place quoting has to be right. `TTYSession` runs its command through
        `/bin/sh -c`, so an unquoted hostname or username would be a command
        injection -- these values come from settings, which an administrator
        controls, but "only an admin can reach it" is not a reason to leave a
        shell open.
        """
        argv = ["ssh"]

        # -tt forces a PTY even though ssh's stdin is already one; without it,
        # ssh declines to allocate a remote terminal in some configurations and
        # the interactive shell never appears.
        argv += ["-tt"]
        argv += ["-p", str(int(self.port))]
        argv += ["-o", f"StrictHostKeyChecking={self.strict_host_key_checking}"]

        if self.known_hosts_file:
            argv += ["-o", f"UserKnownHostsFile={self.known_hosts_file}"]

        if keepalive_interval > 0:
            # Equivalent to paramiko's transport.set_keepalive.
            argv += ["-o", f"ServerAliveInterval={int(keepalive_interval)}"]
            argv += ["-o", "ServerAliveCountMax=3"]

        if self.identity_file:
            # An explicit key, and only that key.
            argv += ["-i", self.identity_file]
            argv += ["-o", "IdentitiesOnly=yes"]
            argv += ["-o", "PreferredAuthentications=publickey"]
            argv += ["-o", "BatchMode=yes"]
        else:
            # Match paramiko's allow_agent=False, look_for_keys=False: use the
            # credentials we were given and nothing the ambient environment
            # happens to offer.
            argv += ["-o", "PreferredAuthentications=password,keyboard-interactive"]
            argv += ["-o", "IdentitiesOnly=yes"]
            argv += ["-o", "IdentityAgent=none"]
            argv += ["-o", "NumberOfPasswordPrompts=1"]

        argv += [f"{self.username}@{self.hostname}"]
        return " ".join(shlex.quote(part) for part in argv)

    # ── lifecycle ─────────────────────────────────────────────────────

    async def connect(self, keepalive_interval: int = 5):
        """Start ssh, authenticate, and leave a usable shell behind.

        Retries twice like the paramiko version, and for the same reason: a
        runner that is still starting refuses the connection rather than
        queueing it.
        """
        errors = 0
        while True:
            try:
                await self._connect_once(keepalive_interval)
                return
            except Exception as e:
                await self._discard_session()
                errors += 1
                if errors < 3:
                    PrintStyle.standard(f"SSH connection attempt {errors}...")
                    self.logger.log(
                        type="info", content=f"SSH connection attempt {errors}..."
                    )
                    await asyncio.sleep(5)
                else:
                    raise e

    async def _connect_once(self, keepalive_interval: int):
        self._session = TTYSession(
            self._ssh_argv(keepalive_interval), cwd=None, echo=False
        )
        await self._session.start()
        self._exit_code = None
        self._authenticated = False

        await self._authenticate()

        # Same shell preparation as the paramiko path: no prompt metadata, no
        # local echo, no pagers waiting on input that will never come.
        initial = f"unset PROMPT_COMMAND PS0; stty -echo; {PAGER_DISABLE_COMMAND}"
        # Written so the literal never appears in the command line itself, only
        # in its output: the shell echoes what it is sent, and `stty -echo` has
        # not run yet at this point in the very command that runs it. The empty
        # quotes vanish when the shell expands the word and survive in the echo.
        cwd_failed_id = uuid.uuid4().hex
        cwd_failed = f"__EAGENT_CWD_FAILED_{cwd_failed_id}__"
        cwd_failed_echo = f'__EAGENT_CWD_FAILED_""{cwd_failed_id}__'
        if self.cwd:
            # Created here rather than by the caller: the caller runs in the
            # application container and can only make directories there, which
            # is the wrong filesystem entirely.
            #
            # And announced on failure rather than ignored. This was `cd X; ...`
            # with a semicolon, so a directory that could not be entered left
            # the shell running in the login home -- every command landing
            # somewhere other than where the tool believed, with nothing said.
            initial = cwd_preamble(self.cwd, cwd_failed_echo) + initial

        # Settle on a marker rather than on silence. The original waits for
        # output to stop arriving, which races against a slow login banner and
        # can return a shell that is not ready. A marker is unambiguous.
        marker = f"__EAGENT_READY_{uuid.uuid4().hex}__"
        await self._session.send(f"{initial}; echo {marker}\n")
        handshake = await self._read_until(
            marker.encode(), timeout=30, what="shell prompt"
        )

        # The marker and the setup output are handshake, not command output.
        self.full_output = b""

        # Read from what the handshake actually returned. `full_output` is the
        # command buffer and is empty at this point.
        if self.cwd and cwd_failed.encode() in handshake:
            raise RuntimeError(
                f"The runner could not enter its working directory {self.cwd!r}. "
                "Refusing to execute, because the alternative is running every "
                "command somewhere else without saying so."
            )

    async def _authenticate(self):
        """Answer the password prompt, or confirm key auth got us in."""
        if self.identity_file:
            self._authenticated = True
            return

        deadline = time.monotonic() + 30
        seen = b""
        while time.monotonic() < deadline:
            chunk = await self._session.read(timeout=_POLL_SECONDS)  # type: ignore[union-attr]
            if chunk is None:
                if self._session and self._session.is_terminated():
                    raise ConnectionError(
                        f"ssh exited before authenticating: {_tail(seen)}"
                    )
                continue

            seen += chunk.encode() if isinstance(chunk, str) else chunk
            _raise_for_known_failures(seen)

            if _PASSWORD_PROMPT.search(seen.rstrip()):
                await self._session.send(self.password + "\n")  # type: ignore[union-attr]
                self._authenticated = True
                return

        raise TimeoutError(f"no password prompt from ssh within 30s: {_tail(seen)}")

    async def _read_until(self, needle: bytes, timeout: float, what: str) -> bytes:
        buffer = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = await self._session.read(timeout=_POLL_SECONDS)  # type: ignore[union-attr]
            if chunk is None:
                if self._session and self._session.is_terminated():
                    raise ConnectionError(f"ssh exited waiting for {what}: {_tail(buffer)}")
                continue
            buffer += chunk.encode() if isinstance(chunk, str) else chunk
            _raise_for_known_failures(buffer)
            if needle in buffer:
                return buffer
        raise TimeoutError(f"timed out waiting for {what}: {_tail(buffer)}")

    async def close(self):
        await self._discard_session()

    async def _discard_session(self):
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass

    # ── the interface code_execution_tool uses ────────────────────────

    async def send_command(self, command: str):
        if not self._session:
            raise Exception("Shell not connected")
        self.full_output = b""
        command = command + "\n"
        self.last_command = command.encode()
        self.trimmed_command_length = 0
        await self._session.send(self.last_command)

    def is_terminated(self) -> bool:
        if not self._session:
            return True
        return self._session.is_terminated()

    def get_exit_code(self) -> int | None:
        if self._exit_code is not None:
            return self._exit_code
        if self._session is None:
            return None
        # This is ssh's own exit status, which is the remote shell's when the
        # session ends normally -- the same thing paramiko's recv_exit_status
        # reports.
        self._exit_code = self._session.get_exit_code()
        return self._exit_code

    async def read_output(
        self, timeout: float = 0, reset_full_output: bool = False
    ) -> Tuple[str, str]:
        """Drain whatever the shell has produced.

        Same contract as the paramiko version: returns (everything since the
        last command, just this call's slice), both cleaned of ANSI noise.
        """
        if not self._session:
            raise Exception("Shell not connected")

        if reset_full_output:
            self.full_output = b""

        partial_output = b""
        drained = 0
        start_time = time.time()

        while timeout <= 0 or time.time() - start_time < timeout:
            chunk = await self._session.read(timeout=_POLL_SECONDS)
            if chunk is None:
                # Nothing further is ready. The paramiko version leaves the loop
                # on `not recv_ready()`; this is the same condition.
                break
            data = chunk.encode() if isinstance(chunk, str) else chunk
            partial_output = _keep_ends(partial_output, data)
            self.full_output = _keep_ends(self.full_output, data)
            drained += len(data)
            if drained >= _DRAIN_BYTES:
                # A session that always has more never says "nothing further".
                break

        return (
            clean_string(self.full_output.decode("utf-8", errors="replace")),
            clean_string(partial_output.decode("utf-8", errors="replace")),
        )


#: What is kept of a command's output at each end. The tool shows the model
#: the first and the last half-million characters of it and drops the middle
#: (`fix_full_output`), so nothing it shows is lost by keeping no more here.
OUTPUT_KEEP_BYTES = 1024 * 1024
#: The most one `read_output` call takes before it returns.
_DRAIN_BYTES = 8 * OUTPUT_KEEP_BYTES


def _keep_ends(buffer: bytes, data: bytes) -> bytes:
    """`buffer + data`, cut to its first and last `OUTPUT_KEEP_BYTES`.

    It grew with every chunk until the next command reset it, so a command
    that printed without end grew the web server with it.
    """
    buffer += data
    if len(buffer) > 2 * OUTPUT_KEEP_BYTES:
        buffer = buffer[:OUTPUT_KEEP_BYTES] + buffer[-OUTPUT_KEEP_BYTES:]
    return buffer


def _raise_for_known_failures(buffer: bytes) -> None:
    """Turn ssh's own diagnostics into exceptions while they are still legible."""
    if _HOST_KEY_FAILURE.search(buffer):
        raise ConnectionError(
            "SSH host key verification failed. The runner's key is not the one "
            f"on record: {_tail(buffer)}"
        )
    if _AUTH_FAILURE.search(buffer):
        raise ConnectionError(f"SSH connection rejected: {_tail(buffer)}")


def _tail(buffer: bytes, limit: int = 300) -> str:
    return clean_string(buffer.decode("utf-8", errors="replace"))[-limit:].strip()
