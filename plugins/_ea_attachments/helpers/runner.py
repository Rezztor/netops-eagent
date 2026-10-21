"""Getting a file out of wherever the agent's code actually ran.

Agent code does not run beside the application. It runs in the runner
container, under this user's own Linux account, in a /workspace that is a
tmpfs. It shares no volume with the application, so a path the agent reports is
not a path the web server -- or the browser -- can open.

The application image has `ssh` and neither `scp` nor `sftp`, so the file comes
back through the command channel base64-encoded. That is why there is a size
limit: the content passes through memory twice on the way.

The endpoint is resolved exactly as code execution resolves it, so an attachment
is fetched from the same place the file was written and never from a different
user's runner.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import shlex
from dataclasses import dataclass

from plugins._code_execution.tools.code_execution_tool import (
    ENV_RUNNER_HOST,
    ENV_RUNNER_HOST_KEY_CHECKING,
    ENV_RUNNER_IDENTITY_FILE,
    ENV_RUNNER_KNOWN_HOSTS,
    ENV_RUNNER_PORT,
    ENV_RUNNER_USER,
)


class FetchError(RuntimeError):
    """The file could not be retrieved. Never falls back to a local read."""


@dataclass(slots=True)
class Endpoint:
    host: str
    port: int
    username: str
    identity_file: str | None
    known_hosts_file: str | None
    strict_host_key_checking: str


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


async def resolve_endpoint(owner: str | None) -> Endpoint | None:
    """Where this user's agent code runs, or None when it runs locally.

    None means a development instance executing in the application's own
    container, where the file is simply on this filesystem.
    """
    from plugins._code_execution.helpers import runner_pool

    pool = runner_pool.get_pool()
    if pool is not None:
        # Per-user runner. Acquiring rather than looking up is deliberate: it
        # may have been reclaimed since the command ran, and the alternative to
        # re-establishing it is failing to deliver a file the user was promised.
        #
        # It must be the same pool the execution used. Falling through to the
        # statically configured account here would fetch the file as `eagent`
        # from a home that account does not own -- which on a shared runner with
        # a persistent workspace is a cross-user read.
        endpoint = await asyncio.to_thread(pool.acquire, owner)
        await asyncio.to_thread(pool.touch, owner)
        return Endpoint(
            host=endpoint.host,
            port=endpoint.port,
            username=endpoint.username,
            identity_file=endpoint.identity_file,
            known_hosts_file=endpoint.known_hosts_file,
            # Pinned from the pod's own logs, so there is no first use to trust.
            strict_host_key_checking="yes",
        )

    host = _env(ENV_RUNNER_HOST)
    if not host:
        return None

    return Endpoint(
        host=host,
        port=int(_env(ENV_RUNNER_PORT) or 22),
        username=_env(ENV_RUNNER_USER) or "eagent",
        identity_file=_env(ENV_RUNNER_IDENTITY_FILE),
        known_hosts_file=_env(ENV_RUNNER_KNOWN_HOSTS),
        strict_host_key_checking=_env(ENV_RUNNER_HOST_KEY_CHECKING) or "accept-new",
    )


def _ssh_argv(endpoint: Endpoint) -> list[str]:
    argv = [
        "ssh",
        # -T, not -tt: this is a single command whose stdout is binary. A PTY
        # would translate newlines and corrupt the payload.
        "-T",
        "-p", str(int(endpoint.port)),
        "-o", f"StrictHostKeyChecking={endpoint.strict_host_key_checking}",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
    ]
    if endpoint.known_hosts_file:
        argv += ["-o", f"UserKnownHostsFile={endpoint.known_hosts_file}"]
    if endpoint.identity_file:
        argv += ["-i", endpoint.identity_file, "-o", "IdentitiesOnly=yes",
                 "-o", "PreferredAuthentications=publickey"]
    argv.append(f"{endpoint.username}@{endpoint.host}")
    return argv


async def _run(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise FetchError("The runner did not answer in time.") from None
    return process.returncode or 0, out, err


async def remote_size(endpoint: Endpoint, path: str) -> int:
    """Bytes, or raise. Checked before reading so an oversized file is refused
    rather than pulled into memory and then rejected."""
    quoted = shlex.quote(path)
    # wc -c rather than stat: portable across the busybox and coreutils images
    # a runner might be built from.
    command = f"test -f {quoted} && wc -c < {quoted}"
    code, out, err = await _run([*_ssh_argv(endpoint), command], timeout=30)
    if code != 0:
        raise FetchError(
            f"No readable file at {path} on the runner."
            + (f" {err.decode('utf-8', 'replace').strip()}" if err.strip() else "")
        )
    try:
        return int(out.decode("utf-8", "replace").strip())
    except ValueError:
        raise FetchError(f"Could not measure {path} on the runner.") from None


async def fetch(endpoint: Endpoint, path: str, max_bytes: int) -> bytes:
    """The file's content, or raise."""
    size = await remote_size(endpoint, path)
    if size > max_bytes:
        raise FetchError(
            f"{path} is {size / 1_048_576:.1f} MB, over the {max_bytes / 1_048_576:.0f} MB limit."
        )

    quoted = shlex.quote(path)
    # -w0 keeps it one line; base64 without it wraps and some implementations
    # differ on where.
    command = f"base64 -w0 -- {quoted} 2>/dev/null || base64 -- {quoted}"
    code, out, err = await _run([*_ssh_argv(endpoint), command], timeout=180)
    if code != 0:
        raise FetchError(
            f"Could not read {path} from the runner."
            + (f" {err.decode('utf-8', 'replace').strip()}" if err.strip() else "")
        )

    try:
        content = base64.b64decode(b"".join(out.split()), validate=True)
    except (binascii.Error, ValueError):
        raise FetchError(f"The runner returned unreadable content for {path}.") from None

    if len(content) != size:
        # A shell that printed a warning into stdout, or a truncated read.
        raise FetchError(
            f"{path} came back as {len(content)} bytes but measured {size}."
        )
    return content
