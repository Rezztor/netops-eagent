"""Agent code reaches the interpreter on descriptor 3, never in its arguments.

The code carries the secrets resolved into it, and the runner is one container
for every account with one PID namespace (ADR 0038): `ipython -c <code>` put the
code, and the secret, in every other slot's `ps`. Proven live in a runner.
"""

import asyncio
import subprocess

import pytest

from plugins._code_execution.tools import code_execution_tool
from plugins._code_execution.tools.code_execution_tool import CodeExecution

CODE = "token = 'ghp_s3cr3t_value'\nprint(\"it's\", token, '!x $HOME `id`')\n"


def _sent(method):
    tool = CodeExecution.__new__(CodeExecution)
    tool.format_command_for_output = lambda text: ""
    commands = []

    async def terminal_session(cfg, session, command, reset, prefix):
        commands.append(command)

    tool.terminal_session = terminal_session
    asyncio.run(getattr(tool, method)({}, session=0, code=CODE))
    return commands[0]


@pytest.mark.parametrize("method", ["execute_python_code", "execute_nodejs_code"])
def test_the_command_sent_to_the_shell_holds_no_code(method):
    command = _sent(method)
    assert "ghp_s3cr3t_value" not in command and "token" not in command


def test_the_code_arrives_intact_on_descriptor_3():
    # A real bash, the shell both the runner and the application run the command in.
    command = code_execution_tool._code_on_fd3("cat /dev/fd/3", CODE)
    out = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True).stdout
    assert out == CODE


def test_a_large_payload_survives_a_shell_that_is_still_busy():
    """While bash runs the previous command the terminal is in canonical mode,
    and the line discipline drops what is past 4095 bytes of one line: a 25 KB
    payload sent straight after the handshake hung bash at `>` in a lab run.
    """
    import hashlib

    from plugins._code_execution.helpers.tty_session import TTYSession

    code = "x = 1\n" * 6000  # 36 KB, 48 KB of base64
    command = code_execution_tool._code_on_fd3("sha256sum /dev/fd/3", code)
    assert max(len(line) for line in command.split("\n")) < 4000

    async def run():
        session = TTYSession("/bin/bash --noprofile --norc -i")
        await session.start()
        await session.read_full_until_idle(idle_timeout=0.05, total_timeout=1)
        await session.sendline("sleep 1")  # busy: readline is not reading yet
        await session.sendline(command)
        out = ""
        try:
            while hashlib.sha256(code.encode()).hexdigest() not in out:
                out += await asyncio.wait_for(session.read(), timeout=10) or ""
        finally:
            await session.close()

    asyncio.run(run())
