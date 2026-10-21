"""How much of a session's output the web server holds.

Agent code runs on the runner; its output is read here. A session printing
without end -- `yes`, `cat /dev/zero | base64` -- filled an unbounded queue
whether or not anything was reading it, and every poll appended to a buffer
that only a new command emptied. The web container has a memory limit and one
process serves every account, so one account with code execution could have
it killed for all of them.
"""

import asyncio

import pytest

from plugins._code_execution.helpers import shell_openssh, tty_session


#: The fakes below never end by themselves, so they end the test: enough reads
#: to show the bound was passed, few enough that an unbounded reader stays small.
GIVE_UP_AFTER = 20_000


class _Endless:
    """A child process whose stdout never ends, counting what was taken from it."""

    def __init__(self):
        self.taken = 0
        self.stdout = self

    async def read(self, size):
        self.taken += 1
        if self.taken > GIVE_UP_AFTER:
            return b""  # end of output: the pump stops
        await asyncio.sleep(0)
        return b"y" * size


@pytest.mark.asyncio
async def test_output_nobody_reads_stops_being_taken_from_the_process():
    spawn = tty_session.TTYSession.__new__(tty_session.TTYSession)
    spawn.encoding, spawn._proc = "utf-8", _Endless()
    spawn._buf = tty_session.new_output_queue()

    pump = asyncio.create_task(spawn._pump_stdout())
    await asyncio.sleep(0.5)
    held = spawn._buf.qsize()
    pump.cancel()

    assert held <= tty_session.MAX_PENDING_CHUNKS, "the queue grew past its bound"
    assert spawn._proc.taken <= tty_session.MAX_PENDING_CHUNKS + 1, (
        "output went on being read from a session nobody was reading: the process must be left to wait"
    )


class _Scripted:
    """A session that hands out the chunks it was given, then says nothing is ready."""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, timeout=None):
        return self.chunks.pop(0) if self.chunks else None


def _shell(chunks):
    shell = shell_openssh.OpenSSHInteractiveSession.__new__(shell_openssh.OpenSSHInteractiveSession)
    shell._session, shell.full_output = _Scripted(chunks), b""
    return shell


@pytest.mark.asyncio
async def test_a_long_output_keeps_its_start_and_its_end_and_no_more():
    half = shell_openssh.OUTPUT_KEEP_BYTES
    shell = _shell(["START-" + "a" * half, "b" * (3 * half), "c" * half + "-END"])

    full, partial = await shell.read_output()

    assert len(shell.full_output) <= 2 * half
    assert full.startswith("START-") and full.endswith("-END")
    assert partial.startswith("START-") and partial.endswith("-END") and len(partial) <= 2 * half


@pytest.mark.asyncio
async def test_a_short_output_is_kept_whole():
    shell = _shell(["one\n", "two\n"])
    assert await shell.read_output() == ("one\ntwo\n", "one\ntwo\n")


@pytest.mark.asyncio
async def test_draining_a_session_that_never_stops_still_returns():
    """`read_output()` reads until nothing is ready, and a session that always has more never said so."""

    class _Always:
        reads = 0

        async def read(self, timeout=None):
            self.reads += 1
            if self.reads > 2_000:
                raise AssertionError("read_output went on draining a session that never stops")
            return "y" * 65536

    shell = _shell([])
    shell._session = _Always()

    full, _ = await shell.read_output()

    assert len(full) <= 2 * shell_openssh.OUTPUT_KEEP_BYTES
