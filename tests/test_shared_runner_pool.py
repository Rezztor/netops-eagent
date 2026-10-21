"""One Linux account per user on a shared runner.

The isolation is the uid: sshd resolves `%u` before it reads
`authorized_keys`, so a key registered for one slot cannot open another's
session, and the home is 0700 owned by that account.

The kernel half of that was verified against a live container on 2026-09-18 --
alice's key on bob's slot is `Permission denied (publickey)`, and bob's
directory is unreadable, unlistable and unwritable from alice's session. What
these tests hold is the half that lives in this repository: that each owner gets
their own slot, keeps it, and that the ways it can go wrong fail closed.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from plugins._code_execution.helpers import runner_pool


SLOTS = 4


@pytest.fixture
def keys_dir(tmp_path: Path) -> Path:
    """What the runner's entrypoint publishes into the shared key volume."""
    for index in range(SLOTS):
        slot = tmp_path / f"eagent-{index:03d}"
        slot.mkdir()
        (slot / "authorized_keys").write_text("")
    (tmp_path / "host_key.pub").write_text("ssh-ed25519 AAAAFAKEHOSTKEY runner\n")
    return tmp_path


@pytest.fixture
def pool(database, keys_dir, monkeypatch):
    monkeypatch.setenv(runner_pool.ENV_RUNNER_SLOTS, str(SLOTS))
    return runner_pool.SharedRunnerPool(host="runner", keys_dir=str(keys_dir))


def test_each_owner_gets_their_own_slot(pool):
    assert pool.acquire("alice").username != pool.acquire("bob").username


def test_an_owner_keeps_the_same_slot(pool):
    """The workspace persists, so the slot has to as well.

    A user who came back to a different slot would find an empty home and leave
    their files stranded in one nothing points at.
    """
    first = pool.acquire("alice")
    assert pool.acquire("alice").username == first.username


def test_the_endpoint_names_the_slots_own_home(pool):
    """The session lands where the account can actually write.

    `_runner_cwd` used to derive `/workspace/<user_id>`, which on a shared
    runner is a directory that does not exist and is owned by nobody. The pool
    reports the home instead, because it is the thing that knows.
    """
    endpoint = pool.acquire("alice")
    assert endpoint.workspace == f"/workspace/{endpoint.username}"


def test_every_slot_gets_its_own_key(pool):
    alice, bob = pool.acquire("alice"), pool.acquire("bob")
    registered = lambda e: (Path(e.known_hosts_file).parent.name, e.identity_file)  # noqa: E731
    assert alice.identity_file != bob.identity_file
    assert registered(alice) != registered(bob)


def test_the_registered_key_is_readable_by_a_capability_less_sshd(pool, keys_dir):
    """0644, and it matters.

    sshd runs under cap_drop ALL, so it holds no CAP_DAC_OVERRIDE and simply
    cannot read a 0600 file owned by another uid. The symptom would be
    `Permission denied (publickey)`, which says nothing about permissions.
    """
    endpoint = pool.acquire("alice")
    written = keys_dir / endpoint.username / "authorized_keys"
    assert oct(written.stat().st_mode)[-3:] == "644"
    assert written.read_text().startswith("ssh-ed25519 ")


def test_the_private_half_stays_private(pool):
    assert oct(Path(pool.acquire("alice").identity_file).stat().st_mode)[-3:] == "600"


def test_the_host_key_is_pinned_before_the_first_connection(pool):
    """No trust-on-first-use, which is what both compose files accept today.

    The entrypoint publishes the public half beside the key directories; the
    private half never leaves the runner.
    """
    endpoint = pool.acquire("alice")
    assert Path(endpoint.known_hosts_file).read_text().startswith("[runner]:2222 ssh-ed25519 ")


def test_an_exhausted_pool_refuses_rather_than_sharing(pool):
    """The pool is fixed at the size the image was built with.

    Refusing is the whole point: the alternative is putting two accounts in one
    home, which is the thing this replaced.
    """
    for index in range(SLOTS):
        pool.acquire(f"user{index}")
    with pytest.raises(runner_pool.RunnerUnavailable):
        pool.acquire("one-too-many")


def test_concurrent_first_acquires_for_one_owner_settle_on_one_slot(pool):
    """Two requests for a new user arrive together -- chat and an attachment.

    Both find no row and both try to claim. Without the re-read one of them
    surfaces a raw UniqueViolation instead of a runner, and the caller sees a
    database error where it expects RunnerUnavailable or an endpoint.
    """
    seen, errors = [], []

    def claim():
        try:
            seen.append(pool.acquire("racer").username)
        except Exception as exc:  # noqa: BLE001 - the point is that there are none
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(set(seen)) == 1


def test_an_empty_table_is_refilled_rather_than_read_as_full(pool, database):
    """Seeded rows are data, and data gets truncated -- by a restore, and by
    this suite between cases. An empty pool is indistinguishable from a full one
    at the point of the claim, and the failure would be "no slots available" on
    a box with 128 idle ones."""
    from helpers import db

    with db.unscoped_transaction() as conn:
        conn.execute("DELETE FROM runner_slots")
    assert pool.acquire("alice").username == "eagent-000"


def test_a_missing_key_directory_refuses_rather_than_sharing_an_account(database, monkeypatch, tmp_path):
    """A deployment that pulled the new image without re-wiring compose.

    Falling through to the shared `eagent` account would put every user in one
    home -- silently, and looking exactly like success.
    """
    monkeypatch.setenv(runner_pool.ENV_RUNNER_SLOTS, str(SLOTS))
    empty = runner_pool.SharedRunnerPool(host="runner", keys_dir=str(tmp_path))
    with pytest.raises(runner_pool.RunnerUnavailable):
        empty.acquire("alice")


def test_no_keys_directory_at_all_refuses(database, monkeypatch):
    monkeypatch.delenv(runner_pool.ENV_KEYS_DIR, raising=False)
    monkeypatch.setenv(runner_pool.ENV_RUNNER_SLOTS, str(SLOTS))
    with pytest.raises(runner_pool.RunnerUnavailable):
        runner_pool.SharedRunnerPool(host="runner").acquire("alice")


# --- handing a slot back -----------------------------------------------------
#
# `_as_slot` is the one place this module reaches the runner, so it is the seam.
# These record the commands that cross it and in what order, because the order
# is the property: a wipe that runs before the kill can be undone by a process
# that outlives it.


@pytest.fixture
def ran(pool, monkeypatch):
    """Every command release() sends to the runner, in order."""
    import subprocess

    sent: list[str] = []

    def fake(slot, command, timeout=300):
        sent.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0)

    monkeypatch.setattr(pool, "_as_slot", fake)
    return sent


def test_release_kills_the_outgoing_account_before_emptying_its_home(pool, ran):
    """Revoking a key does not close a session that is already open.

    OpenSSH reads `authorized_keys` once, at authentication, so a revoked key
    leaves every established session running -- under a uid that is about to be
    handed to somebody else. The kill also has to come *first*: a process that
    survives the wipe writes files back into a home the next claimant is already
    using.
    """
    pool.acquire("alice")
    pool.release("alice")

    # `kill -9 -1` and not `pkill -u`: the kernel signals every process of the
    # account in one pass that no fork can slip through. pkill reads the list
    # and then signals, and a process that forks faster than that survives it.
    assert "kill -9 -1" in ran, "the outgoing account's processes are left running"
    kill = ran.index("kill -9 -1")
    wipe = next(i for i, c in enumerate(ran) if "-delete" in c)
    assert kill < wipe, "a wipe before the kill can be undone by what survives it"


def test_release_puts_the_home_mode_back(pool, ran):
    """`0700` is applied once, at image build, and the account can widen it.

    `find -mindepth 1 -delete` deliberately leaves the directory itself alone,
    so a `chmod 0777 $HOME` outlives its author and the next claimant inherits a
    mode they never chose and never see.
    """
    pool.acquire("alice")
    pool.release("alice")
    assert any("chmod 0700" in c for c in ran)


def test_release_empties_what_the_account_left_in_the_shared_directories(pool, ran):
    """`/tmp` is one tmpfs for every slot, and the next claimant has this uid.

    A file a tool wrote there is 0600 and owned by the account, so the home
    being empty does not make the slot clean: whoever claims it next owns that
    file and can read it. The delete alone is not the proof -- `find` fails on
    other accounts' private directories -- so the wipe has to end by checking.
    """
    pool.acquire("alice")
    pool.release("alice")
    wipe = next(c for c in ran if "/workspace/eagent-000" in c)
    for shared in ("/tmp", "/home/eagent", "/dev/shm"):
        assert shared in wipe
    assert wipe.count("-user eagent-000 -delete") == 1
    assert "-user eagent-000 2>/dev/null | head -n 1" in wipe, "deleted, and then checked"


def test_an_unwritable_directory_cannot_retire_a_slot(pool, ran):
    """`chmod 000 d` in the home made `find -delete` fail, and the slot left the
    fixed pool for good. Write permission goes back on before the delete."""
    pool.acquire("alice")
    pool.release("alice")
    wipe = next(c for c in ran if "/workspace/eagent-000" in c)
    assert wipe.index("chmod -R u+rwx /workspace/eagent-000") < wipe.index(
        "find /workspace/eagent-000 -mindepth 1 -delete"
    )
    assert wipe.index("-type d -exec chmod -R u+rwx") < wipe.index("-user eagent-000 -delete")


def test_the_wipe_fails_while_a_process_of_the_account_survives(pool, ran):
    """The kill runs over its own connection and is not checked; if it never
    arrived, the wipe is what notices, and the slot is held back."""
    pool.acquire("alice")
    pool.release("alice")
    wipe = next(c for c in ran if "/workspace/eagent-000" in c)
    survivors = runner_pool._SURVIVORS.format(slot="eagent-000")
    assert wipe.startswith(f"{survivors} | xargs -r kill -9")
    assert wipe.endswith(f'&& [ -z "$({survivors})" ]')


def test_the_wipe_removes_the_accounts_system_v_ipc_and_checks_it_is_gone(pool, ran):
    """A shared memory segment outlives every process and lives in no
    directory, so the next claimant of the uid could read it (agent-safety
    review S10). Run the check for real in a private IPC namespace: it fails
    while a segment of the account is left, and passes after `ipcrm --all`."""
    import getpass
    import re
    import shutil
    import subprocess

    pool._wipe(getpass.getuser())
    wipe = ran[-1]
    check = re.search(r'\[ -z "\$\(ipcs .*?\)" \]', wipe).group(0)
    assert wipe.index("ipcrm --all") < wipe.index(check)
    if not (shutil.which("unshare") and shutil.which("ipcmk")):
        pytest.skip("needs unshare and ipcmk")
    script = f"ipcmk -M 1024 >/dev/null; {check} && echo clean || echo left; ipcrm --all; {check} && echo clean || echo left"
    done = subprocess.run(["unshare", "-c", "--ipc", "sh", "-c", script], capture_output=True, text=True, timeout=30)
    if done.returncode and "unshare" in done.stderr:
        pytest.skip(f"no private IPC namespace here: {done.stderr.strip()}")
    assert done.stdout.split() == ["left", "clean"], done.stdout + done.stderr


def test_the_survivor_list_names_other_sessions_and_spares_its_own():
    """Run the predicate for real, as the current user: a process in another
    session is a survivor; the shell running the check, its children and its
    parent (sshd on the runner, pytest here) are not."""
    import getpass
    import subprocess

    other = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        survivors = runner_pool._SURVIVORS.format(slot=getpass.getuser())
        out = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", f"echo me=$$; {survivors}"],
            capture_output=True, text=True, start_new_session=True, check=True,
        ).stdout.split()
        me = out[0].removeprefix("me=")
        pids = set(out[1:])
        assert str(other.pid) in pids
        assert me not in pids
        assert str(os.getpid()) not in pids
    finally:
        other.kill()
        other.wait()


def test_a_claim_without_an_owner_is_refused(pool):
    """`owner_id = NULL` matches nothing, so the first free slot came back, still
    free -- the same one for every such caller, and for its next real owner."""
    with pytest.raises(runner_pool.RunnerUnavailable):
        pool.acquire(None)
    with pytest.raises(runner_pool.RunnerUnavailable):
        pool.acquire("")


def test_a_freed_slot_goes_back_to_the_pool(pool, ran):
    pool.acquire("alice")
    pool.release("alice")
    assert pool.acquire("bob").username == "eagent-000"


def _row(slot):
    from helpers import db

    with db.unscoped_transaction() as conn:
        row = conn.execute(
            "SELECT owner_id, needs_wipe FROM runner_slots WHERE slot = %s", (slot,)
        ).fetchone()
    return (row[0], row[1]) if not isinstance(row, dict) else (row["owner_id"], row["needs_wipe"])


def test_release_takes_the_slot_from_its_owner_before_it_empties_it(pool, monkeypatch):
    """Reading the row and freeing it last let an acquire in between the wipe
    and the revoke: its session survived, and the uid went to the next account
    with it running (agent-safety review, 2026-10-19). While the kill and the
    wipe run, the slot must belong to nobody and be offered to nobody."""
    import subprocess

    seen = {}

    def fake(slot, command, timeout=300):
        if "kill -9 -1" in command:
            seen["row"] = _row(slot)
            seen["same owner"] = pool.acquire("alice").username
            seen["another"] = pool.acquire("bob").username
        return subprocess.CompletedProcess(args=[], returncode=0)

    assert pool.acquire("alice").username == "eagent-000"
    monkeypatch.setattr(pool, "_as_slot", fake)
    pool.release("alice")

    assert seen["row"] == (None, True), "the slot was still claimable while it was being emptied"
    assert "eagent-000" not in (seen["same owner"], seen["another"])
    assert _row("eagent-000") == (None, False), "a slot emptied cleanly goes back to the pool"


def test_a_failed_wipe_keeps_the_slot_out_of_the_pool(pool, monkeypatch):
    import subprocess

    pool.acquire("alice")
    monkeypatch.setattr(
        pool, "_as_slot",
        lambda slot, command, timeout=300: subprocess.CompletedProcess(args=[], returncode=1),
    )
    pool.release("alice")
    assert _row("eagent-000") == (None, True)
    assert pool.acquire("bob").username != "eagent-000"


def test_an_acquire_that_lost_its_slot_to_a_release_registers_no_key(pool, keys_dir, monkeypatch, ran):
    """An acquire whose claim landed just before the release took the row
    must not write its key afterwards: that key would open a session behind
    the wipe."""
    pool.acquire("alice")
    pool.release("alice")
    (keys_dir / "eagent-000" / "authorized_keys").write_text("")
    monkeypatch.setattr(pool, "_claim", lambda owner: "eagent-000")

    with pytest.raises(runner_pool.RunnerUnavailable, match="released"):
        pool.acquire("alice")
    assert (keys_dir / "eagent-000" / "authorized_keys").read_text() == ""
    assert _row("eagent-000") == (None, False), "the slot the release emptied is not held back for it"


def test_a_release_waits_for_an_acquire_that_is_registering_its_key(pool, monkeypatch, ran):
    """Two operations at once against the database: an acquire in the middle of
    writing its key holds the row, and the release takes it only afterwards --
    so the kill and the wipe always come after the last key a session could use."""
    pool.acquire("alice")
    real = pool._register
    writing, finish = threading.Event(), threading.Event()
    order: list[str] = []

    def slow(slot, public_key):
        writing.set()
        assert finish.wait(10)
        real(slot, public_key)
        order.append("registered")

    monkeypatch.setattr(pool, "_register", slow)
    acquirer = threading.Thread(target=pool.acquire, args=("alice",))
    acquirer.start()
    assert writing.wait(10)

    releaser = threading.Thread(target=pool.release, args=("alice",))
    releaser.start()
    releaser.join(1.0)
    assert releaser.is_alive(), "the release did not wait for the key being written"
    assert _row("eagent-000") == ("alice", False)

    finish.set()
    acquirer.join(10)
    releaser.join(10)
    order.append("killed" if "kill -9 -1" in ran else "not killed")
    assert order == ["registered", "killed"]
    assert _row("eagent-000") == (None, False)


def test_release_replaces_the_session_key_before_it_connects(pool, keys_dir, monkeypatch):
    """An endpoint handed out before the slot was taken must not be able to
    open a session after the wipe: the release's own key replaces it."""
    import subprocess

    session_key = pool.acquire("alice")
    session_public = Path(session_key.identity_file).with_suffix(".pub").read_text().strip()
    real_run = subprocess.run
    registered: list[str] = []

    def run(argv, *args, **kwargs):
        if argv[0] == "ssh":
            registered.append((keys_dir / "eagent-000" / "authorized_keys").read_text().strip())
            return subprocess.CompletedProcess(args=argv, returncode=0)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(runner_pool.subprocess, "run", run)
    pool.release("alice")
    assert registered and session_public not in registered


def test_the_sweep_releases_a_slot_whose_account_no_longer_exists(pool, ran):
    """An account deleted while its release could not run, or a slot claimed by
    its agent while it was still finishing: nobody else would give it back."""
    from helpers import db

    with db.unscoped_transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES ('bob', 'bob', '', 'user')"
        )
    pool.acquire("ghost")
    bob = pool.acquire("bob").username

    assert "eagent-000" in pool.sweep()
    assert _row("eagent-000") == (None, False)
    assert "kill -9 -1" in ran
    assert _row(bob) == ("bob", False), "a living account's slot was taken"


def test_the_sweep_empties_a_slot_whose_wipe_never_finished(pool, monkeypatch):
    """The runner was down when the account was deleted, or the application
    stopped half way through a release: the slot is held for a wipe nobody
    would ever retry (security review of the S5 fix, 2026-10-19). The sweep
    retries it, but not one whose release is still running, and a wipe that
    fails again keeps it held until the next interval."""
    import subprocess

    from helpers import db

    result = {"rc": 1}
    sent: list[str] = []

    def fake(slot, command, timeout=300):
        sent.append(slot)
        return subprocess.CompletedProcess(args=[], returncode=result["rc"])

    monkeypatch.setattr(pool, "_as_slot", fake)
    pool.acquire("alice")
    pool.release("alice")                      # the runner is down: the wipe fails
    assert _row("eagent-000") == (None, True)
    sent.clear()
    assert pool.sweep() == [] and sent == [], "retried a slot whose wipe had only just begun"

    with db.unscoped_transaction() as conn:
        conn.execute("UPDATE runner_slots SET last_seen = now() - interval '1 hour' WHERE slot = 'eagent-000'")
    assert pool.sweep() == [] and "eagent-000" in sent and _row("eagent-000") == (None, True)
    sent.clear()
    assert pool.sweep() == [] and sent == [], "a failing wipe is retried at every sweep"

    result["rc"] = 0                            # the runner is back
    with db.unscoped_transaction() as conn:
        conn.execute("UPDATE runner_slots SET last_seen = NULL WHERE slot = 'eagent-000'")
    assert pool.sweep() == ["eagent-000"]
    assert _row("eagent-000") == (None, False)


def test_deleting_an_account_stops_its_agents_and_then_empties_its_slot(pool, ran, monkeypatch):
    from helpers import users

    monkeypatch.setenv(runner_pool.ENV_RUNNER_POOL, "shared")
    monkeypatch.setattr(runner_pool, "get_pool", lambda: pool)
    users.create_user("root", "a-long-password-1", "admin")
    alice = users.create_user("alice", "a-long-password-2", "user")
    pool.acquire(alice["id"])

    order: list[str] = []
    real_end = users._end_account_activity
    monkeypatch.setattr(
        users, "_end_account_activity",
        lambda user_id, forget: (order.append("agents stopped"), real_end(user_id, forget)),
    )
    real_release = pool.release
    monkeypatch.setattr(pool, "release", lambda owner: (order.append("released"), real_release(owner)))

    users.delete_user("alice", actor="root")

    assert order == ["agents stopped", "released"]
    assert "kill -9 -1" in ran
    assert _row("eagent-000") == (None, False)


# --- a slot that cannot be registered ----------------------------------------


def test_a_slot_that_cannot_be_registered_is_not_left_claimed(pool, monkeypatch):
    """The claim commits before the key is written, so a failure between them
    used to strand the user on a slot that can never work.

    `_claim`'s fast path returns the row this owner already holds, so every
    later acquire handed back the same dead slot and only manual SQL recovered
    it. Reachable whenever `REZZTOR_RUNNER_SLOTS` exceeds what the image built.
    """
    def refuse(slot, public_key):
        raise runner_pool.RunnerUnavailable("no key directory for this slot")

    monkeypatch.setattr(pool, "_register", refuse)
    with pytest.raises(runner_pool.RunnerUnavailable):
        pool.acquire("alice")

    from helpers import db

    with db.unscoped_transaction() as conn:
        row = conn.execute(
            "SELECT owner_id, needs_wipe FROM runner_slots WHERE slot = 'eagent-000'"
        ).fetchone()
    owner, needs_wipe = (row[0], row[1]) if not isinstance(row, dict) else (row["owner_id"], row["needs_wipe"])
    assert owner is None, "the owner is still holding a slot whose key never landed"
    assert needs_wipe is True, "a slot the image has no account for must leave the rotation"


def test_a_slot_that_cannot_be_registered_does_not_swallow_the_pool(pool, monkeypatch):
    """Holding the dead slot back has to let the *next* claim move past it,
    otherwise every user in turn walks into the same wall."""
    calls = {"n": 0}
    real = pool._register

    def refuse_first(slot, public_key):
        calls["n"] += 1
        if slot == "eagent-000":
            raise runner_pool.RunnerUnavailable("no key directory for this slot")
        return real(slot, public_key)

    monkeypatch.setattr(pool, "_register", refuse_first)
    with pytest.raises(runner_pool.RunnerUnavailable):
        pool.acquire("alice")
    assert pool.acquire("alice").username == "eagent-001"


# --- backend selection -------------------------------------------------------






def test_the_backend_is_chosen_explicitly(monkeypatch):
    """`shared` is the only pool. Silence still means no pool at all.

    The Kubernetes backend went with the chart; an unset variable therefore
    leaves a deployment on its statically configured runner rather than quietly
    selecting something.
    """
    monkeypatch.setenv(runner_pool.ENV_RUNNER_POOL, "shared")
    assert isinstance(runner_pool.get_pool(), runner_pool.SharedRunnerPool)
    monkeypatch.setenv(runner_pool.ENV_RUNNER_POOL, "off")
    assert runner_pool.get_pool() is None
    monkeypatch.delenv(runner_pool.ENV_RUNNER_POOL, raising=False)
    assert runner_pool.get_pool() is None


def test_a_backend_that_no_longer_exists_refuses_rather_than_guessing(monkeypatch):
    """Including `kubernetes`, which a deployment may still have set."""
    for name in ("kubernetes", "docker"):
        monkeypatch.setenv(runner_pool.ENV_RUNNER_POOL, name)
        with pytest.raises(runner_pool.RunnerUnavailable):
            runner_pool.get_pool()
