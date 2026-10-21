"""One Linux account per user on a single shared code-execution runner.

ADR 0006 left the last isolation gap open and named it: a single shared runner
means two users executing code share a workspace, so the per-user separation
Phase 4 enforces at the API does not hold against the code execution tool.
ADR 0020 closed it with a pod per user through the Kubernetes API, which made
per-user execution isolation available on exactly one deployment shape. ADR 0038
keeps the property and drops the orchestrator: the accounts are baked into the
runner image and `SharedRunnerPool` claims one of them per user.

Design notes worth keeping:

**Credentials are per slot, not per deployment.** Each account gets a freshly
generated authorized key, so a user who somehow obtains their own slot's key
still cannot reach anyone else's. A single shared `runner-authorized-key` would
have made every account interchangeable.

**The host key is pinned before the first connection.** The runner's entrypoint
publishes its public host key beside the per-slot key directories, so the
known_hosts entry is written before anything dials and the connection uses
`StrictHostKeyChecking=yes` from the outset. The private half never leaves the
runner. Trust-on-first-use would have been the obvious alternative and is
exactly the weakness the paramiko path had.

**This pool creates nothing.** The accounts already exist, because the runner's
root filesystem is read-only and `useradd` cannot run there, so acquiring is
claiming a row in `runner_slots` and writing one file. Releasing is the half
that has to be thorough, and `release` says why.

**The runner is reached with `ssh`, not a Python SSH library.** The transport is
`shell_openssh.py`; this module only produces the identity, the known_hosts file
and the address that transport needs.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from helpers.print_style import PrintStyle

SSH_PORT = 2222

# A slot is reclaimed after this long without use. Slots are per user and
# sessions are per conversation, so a slot cannot be released when one
# conversation ends -- another may still be using it. Idle reclamation also
# collects slots left claimed by a crash of the application, which explicit
# release alone never would.
IDLE_RECLAIM_SECONDS = 30 * 60


# Every live process of a slot's uid outside the command's own session, minus
# the sshd process that carries that session (the shell's parent). slot-shell
# execs bash, and sshd setsid()s it, so `$$` is the session id. Zombies are
# dead and are left to their reaper.
_SURVIVORS = (
    "ps -o pid=,sid=,stat= -u {slot} "
    "| awk -v me=$$ -v up=$PPID '$2 != me && $1 != up && $3 !~ /^Z/ {{print $1}}'"
)


@dataclass(frozen=True)
class RunnerEndpoint:
    """Everything the SSH transport needs to reach one runner."""

    host: str
    port: int
    username: str
    identity_file: str
    known_hosts_file: str
    slot_name: str
    # Where this owner's work belongs on that runner: the slot's own 0700 home,
    # not the shared workspace root. The caller asks rather than deriving it,
    # because deriving it is how the two disagree -- and a session that lands in
    # a directory it does not own is either a failure or a leak depending on
    # which way the mistake goes.
    workspace: str = "/workspace"


class RunnerUnavailable(RuntimeError):
    """The runner could not be provisioned. Never a reason to fall back.

    Falling back to local execution here would silently return the system to
    running agent code inside the application container, which is the thing this
    module exists to stop.
    """






def generate_keypair(keys_root: Path, name: str) -> dict[str, str]:
    """A key per slot, on disk with 0600, under a directory only we can read.

    Module level rather than a method because the key is the slot's, not the
    pool's. A key per slot rather than per deployment is what stops one leaked
    key opening every account on the runner.
    """
    directory = keys_root / name
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(keys_root, stat.S_IRWXU)
    os.chmod(directory, stat.S_IRWXU)

    private = directory / "id_ed25519"
    if not private.is_file():
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-f", str(private), "-N", "", "-C", name],
            check=True,
            capture_output=True,
        )
    os.chmod(private, stat.S_IRUSR | stat.S_IWUSR)
    public = (private.with_suffix(".pub")).read_text().strip()
    return {
        "private_file": str(private),
        "public": public,
        # Identifies which key a slot was last registered with, so a stale
        # registration is replaced rather than connected to with a key the
        # runner never saw.
        "fingerprint": hashlib.sha256(public.encode()).hexdigest()[:32],
        "dir": str(directory),
    }


ENV_RUNNER_POOL = "REZZTOR_RUNNER_POOL"
ENV_KEYS_DIR = "REZZTOR_RUNNER_KEYS_DIR"
#: How many accounts the runner image was built with. The image is the
#: authority; this is how the application is told.
#:
#: **They can drift, and a comment here used to claim otherwise.** The image's
#: count is `RUNNER_SLOTS` in `docker/runner/Dockerfile`; this one is
#: `REZZTOR_RUNNER_SLOTS` on the application service. They are two independent
#: literals that happen to both say 128, and nothing checks them against each
#: other. Set this higher than the image carries and the extra rows are claimed
#: by real users whose keys can never be registered -- which is why `acquire`
#: gives a slot back rather than leaving a user holding one that cannot work.
ENV_RUNNER_SLOTS = "REZZTOR_RUNNER_SLOTS"
DEFAULT_SLOTS = 128
ENV_RUNNER_HOST = "REZZTOR_RUNNER_HOST"

#: Published by the runner's entrypoint beside the per-slot key directories, so
#: the host key can be pinned before the first connection without reading a
#: container's logs. The private half never leaves the runner.
HOST_KEY_FILE = "host_key.pub"


def _replace_with(target: Path, contents: str) -> None:
    """Replace `target` atomically, via a temporary name nobody else holds.

    `os.replace` is atomic; the temporary file it renames was not private. Both
    writers used one fixed name per slot -- `authorized_keys.tmp` -- so two
    concurrent registrations for the same slot wrote the same path, and whichever
    reached `os.replace` second found its source already renamed away. That
    surfaced as a bare FileNotFoundError out of a function whose only handled
    error is PermissionError: the raw-exception-instead-of-a-runner case that
    test_concurrent_first_acquires_for_one_owner_settle_on_one_slot exists to
    rule out. It caught this intermittently, which is how a race announces
    itself.

    mkstemp in the target's own directory keeps the rename on one filesystem, so
    it stays atomic.
    """
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".authorized_keys.")
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(contents)
        # 0644 is load-bearing, and mkstemp creates 0600: sshd drops every
        # capability, so it has no CAP_DAC_OVERRIDE and simply cannot read a
        # 0600 file owned by another uid.
        os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


class SharedRunnerPool:
    """One Linux account per user on a single shared runner.

    Each user gets an account from a fixed pool baked into the runner image --
    `eagent-000` upward, each with its own uid and a 0700 home under /workspace.
    sshd resolves `%u` before it reads `authorized_keys`, so a key registered
    for one slot cannot open another's session, and the kernel does the rest:
    two users are two uids, not two directories one uid can read either of.

    What this pool does *not* do is create anything. The accounts exist already,
    because the runner's root filesystem is read-only and `useradd` cannot run
    there. Acquiring is therefore claiming a row and writing one file.

    Its costs are real and worth knowing: sessions share a PID namespace, so
    process names and command lines are visible between users even though
    /proc/<pid>/environ and fd are not, and there is no per-user CPU or memory
    limit because there is one container. The retired pod-per-user model had
    both, and ADR 0038 records why the trade is still worth making.
    """

    def __init__(self, host: str | None = None, keys_dir: str | None = None):
        self._host = (host or os.getenv(ENV_RUNNER_HOST) or "").strip()
        keys = (keys_dir or os.getenv(ENV_KEYS_DIR) or "").strip()
        self._keys_dir = Path(keys) if keys else None
        self._keys_root = Path(tempfile.gettempdir()) / "eagent-runner-keys"

    def acquire(self, owner: str) -> RunnerEndpoint:
        if not owner:
            # `owner_id = NULL` matches no row, so the claim would hand out the
            # first free slot and leave it free: every caller without an owner
            # would land in the same home, and so would its next real claimant.
            raise RunnerUnavailable("a runner slot is claimed for an account, and there is none in scope")
        from helpers import db

        slot = self._claim(owner)
        keys = generate_keypair(self._keys_root, slot)
        try:
            # The key goes in while the row is held and still this owner's.
            # release() takes a slot out of its owner's hands before it empties
            # it, and waits for this lock to do so; a key written after that
            # would let a session in behind the wipe, under a uid that is about
            # to be handed to somebody else.
            with db.unscoped_transaction() as conn:
                owned = conn.execute(
                    "SELECT 1 FROM runner_slots WHERE slot = %s AND owner_id = %s FOR UPDATE",
                    (slot, owner),
                ).fetchone()
                if owned:
                    self._register(slot, keys["public"])
        except RunnerUnavailable:
            # The claim is committed by now, in its own transaction, so without
            # this the owner keeps a slot whose key can never be registered --
            # `_claim`'s fast path hands them the same dead slot on every later
            # acquire, and only manual SQL gets them out of it. Give it back,
            # and hold it out of the pool so the next claimant does not walk
            # into the same wall.
            self._hold_back(slot)
            raise
        if not owned:
            raise RunnerUnavailable(f"runner slot {slot} was released while it was being claimed")
        known_hosts = self._pin_host_key(Path(keys["dir"]))
        return RunnerEndpoint(
            host=self._host,
            port=SSH_PORT,
            username=slot,
            identity_file=keys["private_file"],
            known_hosts_file=str(known_hosts),
            slot_name=slot,
            workspace=f"/workspace/{slot}",
        )

    def touch(self, owner: str) -> None:
        """Best effort: a failed refresh must not fail the execution it was
        only recording."""
        try:
            from helpers import db

            with db.transaction() as conn:
                conn.execute(
                    "UPDATE runner_slots SET last_seen = now() WHERE owner_id = %s",
                    (owner,),
                )
        except Exception:  # noqa: BLE001
            pass

    def sweep(self, idle_seconds: int = IDLE_RECLAIM_SECONDS) -> list[str]:
        """Revoke the keys of owners who have gone quiet, and keep their slot.

        The retired pod-per-user pool deleted the pod instead, and could: a
        pod held nothing, because its workspace was an emptyDir. A slot's home
        persists, so freeing the slot would offer somebody else's files to the
        next claimant. What goes cold is the key,
        which is re-registered by the next acquire; what stays is the mapping,
        because the files are still theirs.
        """
        from helpers import db

        revoked: list[str] = []
        with db.unscoped_transaction() as conn:
            rows = conn.execute(
                "SELECT slot FROM runner_slots WHERE owner_id IS NOT NULL "
                "AND last_seen IS NOT NULL "
                "AND last_seen < now() - make_interval(secs => %s)",
                (idle_seconds,),
            ).fetchall()
        for row in rows:
            slot = row[0] if not isinstance(row, dict) else row["slot"]
            if self._revoke(slot):
                revoked.append(slot)

        # A slot still held for an account that no longer exists: deleted while
        # its release could not run, or claimed by an agent of that account
        # that was still finishing. Nobody else will ever give it back.
        with db.unscoped_transaction() as conn:
            rows = conn.execute(
                "UPDATE runner_slots r SET owner_id = NULL, last_seen = now(), needs_wipe = TRUE "
                "WHERE owner_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = r.owner_id) "
                "RETURNING slot"
            ).fetchall()
        # A slot held for a wipe that never finished: the runner was down when
        # its account was deleted, or the application stopped half way through
        # a release. `last_seen` on a held slot is when its wipe last began, so
        # one still running is left to finish, and one that keeps failing is
        # tried again once per `idle_seconds` rather than at every sweep.
        with db.unscoped_transaction() as conn:
            rows += conn.execute(
                "UPDATE runner_slots SET last_seen = now() "
                "WHERE owner_id IS NULL AND needs_wipe "
                "AND (last_seen IS NULL OR last_seen < now() - make_interval(secs => %s)) "
                "RETURNING slot",
                (idle_seconds,),
            ).fetchall()
        for row in rows:
            slot = row[0] if not isinstance(row, dict) else row["slot"]
            if self._empty(slot):
                revoked.append(slot)
        return revoked

    def release(self, owner: str) -> None:
        """Give up a slot: take it from the owner, stop their code, empty the home.

        **The row goes first.** One statement frees it from the owner and marks
        it `needs_wipe`, so from that moment no acquire can reach it -- not this
        owner's fast path, which matches on `owner_id`, and not another
        account's claim, which skips a slot awaiting a wipe. It waits for an
        acquire that is registering a key on the slot, and an acquire that comes
        after it finds the slot no longer theirs. Reading the row and freeing it
        last, as this did until 2026-10-19, let a concurrent acquire register
        its key between the wipe and the revoke: a session opened there
        survived, and the uid went to the next account with it still running.

        Then the order is the opposite of the obvious one. Revoking the key
        first would lock us out of the very session needed to empty the home,
        so the kill and the wipe run first, and only then is the key revoked.

        **Killing comes before wiping, and revoking is not a substitute for
        either.** OpenSSH checks `authorized_keys` when a session is
        established and never again, so revoking a key leaves every session
        already open still running -- and the uid is about to be handed to
        somebody else. A process that outlived the wipe would also write files
        back into a home the next claimant is already using.

        `needs_wipe` is cleared only after a wipe that succeeded. A slot whose
        wipe fails stays held back and is not offered to anybody: it would hand
        a new user a working directory full of the last one's files.
        """
        from helpers import db

        with db.unscoped_transaction() as conn:
            # last_seen = now(): on a held slot it is when its wipe began, which
            # keeps the sweep's retry off a release that is still running.
            row = conn.execute(
                "UPDATE runner_slots SET owner_id = NULL, last_seen = now(), needs_wipe = TRUE "
                "WHERE owner_id = %s RETURNING slot",
                (owner,),
            ).fetchone()
        if not row:
            return
        self._empty(row[0] if not isinstance(row, dict) else row["slot"])

    def _empty(self, slot: str) -> bool:
        """Kill, wipe and revoke a slot already taken from its owner, and return
        it to the pool only if the wipe succeeded."""
        from helpers import db

        self._kill_sessions(slot)
        wiped = self._wipe(slot)
        self._revoke(slot)
        if wiped:
            with db.unscoped_transaction() as conn:
                conn.execute(
                    "UPDATE runner_slots SET needs_wipe = FALSE WHERE slot = %s AND owner_id IS NULL",
                    (slot,),
                )
        else:
            PrintStyle(font_color="yellow", padding=False).print(
                f"runner slot {slot} could not be emptied and is held back rather "
                "than offered to another account. Empty /workspace/"
                f"{slot} on the runner and clear needs_wipe to return it to the pool."
            )
        return wiped

    # -- internals ---------------------------------------------------------

    def _slot_names(self) -> list[str]:
        try:
            count = int(os.getenv(ENV_RUNNER_SLOTS) or DEFAULT_SLOTS)
        except ValueError:
            count = DEFAULT_SLOTS
        return [f"eagent-{i:03d}" for i in range(max(0, count))]

    def _ensure_pool(self, conn) -> None:
        """Materialise the rows for the accounts this image carries.

        The migration seeds them, but seeded rows are data and data gets
        truncated -- by a restore, and by the test suite between cases. A pool
        that reads as empty is indistinguishable from a pool that is full, and
        the failure is "no runner slots available" on a box with 128 idle ones.

        Idempotent, so it also covers the image being rebuilt with more slots:
        the new rows appear the first time the application asks for one.
        """
        names = self._slot_names()
        if not names:
            return
        conn.execute(
            "INSERT INTO runner_slots (slot) SELECT unnest(%s::text[]) "
            "ON CONFLICT (slot) DO NOTHING",
            (names,),
        )

    def _claim(self, owner: str) -> str:
        from helpers import db

        with db.unscoped_transaction() as conn:
            self._ensure_pool(conn)
            row = conn.execute(
                "UPDATE runner_slots SET last_seen = now() WHERE owner_id = %s "
                "RETURNING slot",
                (owner,),
            ).fetchone()
            if row:
                return row[0] if not isinstance(row, dict) else row["slot"]

            # FOR UPDATE SKIP LOCKED so two first-time acquires cannot settle on
            # one row: Postgres does the exclusion rather than the application
            # counting free slots and racing itself.
            row = conn.execute(
                "UPDATE runner_slots SET owner_id = %s, last_seen = now() "
                "WHERE slot = (SELECT slot FROM runner_slots "
                "              WHERE owner_id IS NULL AND NOT needs_wipe "
                "              ORDER BY slot LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING slot",
                (owner,),
            ).fetchone()
            if row:
                return row[0] if not isinstance(row, dict) else row["slot"]

        # Either the pool is full or this owner won a slot in a concurrent
        # transaction whose UNIQUE constraint sent us here. Re-read before
        # deciding which, so a race reads as success rather than exhaustion.
        with db.unscoped_transaction() as conn:
            row = conn.execute(
                "SELECT slot FROM runner_slots WHERE owner_id = %s", (owner,)
            ).fetchone()
        if row:
            return row[0] if not isinstance(row, dict) else row["slot"]
        raise RunnerUnavailable(
            "every runner slot on this appliance is assigned. The pool is fixed "
            "at the size the runner image was built with; freeing one means "
            "deleting an account, and adding more means a new image."
        )

    def _slot_keys_dir(self, slot: str) -> Path:
        if self._keys_dir is None:
            raise RunnerUnavailable(
                f"{ENV_KEYS_DIR} is not set, so a per-user key cannot be "
                "registered with the runner. Refusing rather than falling back "
                "to the shared account, which would put every user in one home."
            )
        return self._keys_dir / slot

    def _register(self, slot: str, public_key: str) -> None:
        """Write the slot's authorized_keys whole, atomically.

        Replace rather than open-and-write: the file is created root-owned by
        the runner's entrypoint, so writing in place depends on ownership we do
        not control, while replacing it needs only the directory. It is also
        atomic, so sshd never reads half a key.

        0644 is load-bearing. sshd runs with cap_drop ALL and therefore has no
        CAP_DAC_OVERRIDE, so a 0600 file owned by another uid is simply
        unreadable to it -- and the symptom is `Permission denied (publickey)`,
        which says nothing about permissions.
        """
        directory = self._slot_keys_dir(slot)
        if not directory.is_dir():
            raise RunnerUnavailable(
                f"the runner published no key directory for {slot}. Either the "
                "runner is not running in slot mode, or the image carries fewer "
                "slots than the database was seeded with."
            )
        target = directory / "authorized_keys"
        try:
            _replace_with(target, f"{public_key}\n")
        except PermissionError as exc:
            # The runner creates these directories root:eagent 0775 so that the
            # application, which runs as uid 1000, can write them; this normally
            # cannot happen -- but a deployment that changes that finds out here, and a bare
            # PermissionError names a temp file rather than the reason.
            raise RunnerUnavailable(
                f"cannot register a key for {slot}: {exc}. The key directory is "
                "written by the runner as root and the application has to be "
                "able to write into it."
            ) from exc

    def _revoke(self, slot: str) -> bool:
        try:
            target = self._slot_keys_dir(slot) / "authorized_keys"
        except RunnerUnavailable:
            return False
        try:
            _replace_with(target, "")
            return True
        except OSError:
            return False

    def _as_slot(self, slot: str, command: str, timeout: int = 300):
        """Run one command on the runner as `slot` itself.

        The account is the only thing that can act inside its own 0700 home: the
        application container has no route there, and the runner's root holds no
        CAP_DAC_OVERRIDE. Returns None when no session could be established.
        """
        # A key of its own, not the one sessions use. Registering it replaces
        # theirs, so an endpoint handed out before the slot was taken from its
        # owner can no longer open a session behind the wipe.
        keys = generate_keypair(self._keys_root, f"{slot}.release")
        try:
            self._register(slot, keys["public"])
            known_hosts = self._pin_host_key(Path(keys["dir"]))
        except RunnerUnavailable:
            return None
        try:
            return subprocess.run(
                [
                    "ssh", "-T",
                    "-p", str(SSH_PORT),
                    "-i", keys["private_file"],
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", f"UserKnownHostsFile={known_hosts}",
                    "-o", "IdentitiesOnly=yes",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10",
                    f"{slot}@{self._host}",
                    command,
                ],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def _kill_sessions(self, slot: str) -> None:
        """Stop everything still running as this account.

        `kill -9 -1`, sent as the slot: the kernel signals every process the
        caller may signal -- every one of this uid -- in a single pass over the
        task list, and a fork cannot land in the middle of it. `pkill -u`, which
        this was, reads the process list and then signals what it read, so a
        process that keeps forking outruns it and survives into the wipe.

        Best effort by construction, and deliberately not checked. The kill
        takes this very session too -- the sshd child that setuid'd into the
        account -- so the connection drops and ssh exits 255 whether or not it
        worked. That is the accepted cost of not needing to exclude our own
        process tree, and it is why this runs *before* the wipe, in its own
        connection: the wipe then gets a clean session with nothing left alive
        to write files back into the home.

        Both commands depend on the runner giving slot accounts
        `/usr/local/bin/slot-shell` as their shell (docker/runner/Dockerfile).
        With bash there, the account's own `~/.bashrc` runs first, and one line
        in it -- `exit 0` -- turned the kill and the wipe into nothing that
        still answered success.
        """
        self._as_slot(slot, "kill -9 -1", timeout=30)

    def _wipe(self, slot: str) -> bool:
        """Empty a slot's home and put its mode back, as that slot.

        The chmod is not redundant. `0700` is applied once, by `install -d` at
        image build; the account owns its home and can widen it, and a
        `chmod 0777 $HOME` survives the delete below -- `-mindepth 1` leaves the
        directory itself alone by design, so the next claimant would inherit a
        mode they never chose and never see. Re-applying it here is the only
        point in the lifecycle that runs between two owners.

        The home is not the only place the account wrote. `/tmp`, `/home/eagent`
        and `/dev/shm` are shared by every slot, and what a tool left there is
        0600 and owned by this uid -- which is the next claimant's uid, so they
        would read it. Errors are ignored on the delete, because `find` also
        fails on other accounts' private directories; what decides is the
        check after it, that nothing owned by this account is left.

        A directory the account made unwritable (`chmod 000 d`) cannot have its
        entries unlinked, which let any user retire their slot from the fixed
        pool on the way out; `u+rwx` is put back on everything it owns first.

        The kill before this ran over its own connection and is not checked, so
        a process can survive it -- that connection failing while this one
        succeeds is enough. This command therefore kills again and passes only
        when no process of the uid is left but its own session and the sshd
        that carries it (`_SURVIVORS`).

        System V shared memory, semaphores and message queues outlive every
        process and live in no directory: a segment the account left is read by
        the next claimant of its uid. `ipcrm --all` as the account removes what
        it may remove, which is its own and nobody else's, and the check after
        it passes only when none of its own is left.
        """
        shared = "/tmp /home/eagent /dev/shm"
        survivors = _SURVIVORS.format(slot=slot)
        result = self._as_slot(
            slot,
            f"{survivors} | xargs -r kill -9 2>/dev/null; sleep 1; "
            "ipcrm --all 2>/dev/null; "
            f"find {shared} -mindepth 1 -user {slot} -type d -exec chmod -R u+rwx {{}} + 2>/dev/null; "
            f"find {shared} -mindepth 1 -user {slot} -delete 2>/dev/null; "
            f"chmod -R u+rwx /workspace/{slot} 2>/dev/null; "
            f"find /workspace/{slot} -mindepth 1 -delete && chmod 0700 /workspace/{slot} "
            f'&& [ -z "$(find {shared} -mindepth 1 -user {slot} 2>/dev/null | head -n 1)" ] '
            f'&& [ -z "$(ipcs 2>/dev/null | awk -v u={slot} \'$3 == u\')" ] '
            f'&& [ -z "$({survivors})" ]',
        )
        return result is not None and result.returncode == 0

    def _hold_back(self, slot: str) -> None:
        """Return a slot to nobody: unclaimed, and out of the rotation.

        `needs_wipe` is the existing "do not offer this to anyone" flag. It was
        named for a home that could not be emptied, and it carries the same
        meaning for a slot the image has no account for -- in both cases handing
        it to the next claimant is the wrong answer, and an operator has to look
        at it before it comes back.
        """
        from helpers import db

        try:
            with db.unscoped_transaction() as conn:
                conn.execute(
                    "UPDATE runner_slots SET owner_id = NULL, last_seen = NULL, "
                    "needs_wipe = TRUE WHERE slot = %s",
                    (slot,),
                )
        except Exception:  # noqa: BLE001 - the caller is already raising
            return
        PrintStyle(font_color="yellow", padding=False).print(
            f"runner slot {slot} could not be registered and was returned to "
            "nobody. The runner image carries fewer accounts than "
            f"{ENV_RUNNER_SLOTS} claims; clear needs_wipe once they agree."
        )

    def _pin_host_key(self, directory: Path) -> Path:
        """Pin from the public half the entrypoint publishes beside the keys.

        The retired pod-per-user pool read this from the pod's logs, which a
        shared runner has no equivalent of. Publishing the public half into the
        key directory keeps the property that matters: the first connection uses
        `StrictHostKeyChecking=yes` against a key we already hold, rather than
        the trust-on-first-use both compose files accept today.
        """
        if self._keys_dir is None:
            raise RunnerUnavailable(f"{ENV_KEYS_DIR} is not set")
        published = self._keys_dir / HOST_KEY_FILE
        try:
            key = published.read_text().strip()
        except OSError as exc:
            raise RunnerUnavailable(
                "the runner has published no host key, so the connection cannot "
                f"be verified: {exc}"
            ) from exc
        known_hosts = directory / "known_hosts"
        known_hosts.write_text(f"[{self._host}]:{SSH_PORT} {key}\n")
        os.chmod(known_hosts, stat.S_IRUSR | stat.S_IWUSR)
        return known_hosts


def get_pool():
    """The pool this deployment uses, or None for a statically configured runner.

    One place decides, because the alternative is each caller re-deriving it and
    two of them disagreeing -- which presents as code execution working in chat
    and not for attachments, or an idle sweep that reclaims nothing.

    `shared` is the only pool now. The Kubernetes one went with the chart on
    2026-09-18: with no cluster to deploy into it was code nothing could reach,
    and per-user isolation no longer needs an orchestrator (ADR 0038). It is in
    the git history if a cluster ever comes back.
    """
    backend = (os.getenv(ENV_RUNNER_POOL) or "off").strip().lower()
    if backend == "shared":
        return SharedRunnerPool()
    if backend == "off":
        return None
    raise RunnerUnavailable(
        f"{ENV_RUNNER_POOL}={backend!r} is not a runner pool. Use 'shared' or 'off'."
    )






def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


