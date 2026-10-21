"""Device configuration backups, encrypted on the executor and never returned in the clear.

A running config holds every hash and community string the device has. ADR 0044 keeps
device secrets away from the model; this keeps them away from the application too. The text
is stored AES-256-GCM under the credential key with the host and id bound as additional data,
and no route returns it raw: the application gets dates, sizes and hashes, a **redacted**
view, or a diff in which every line has been redacted. The only way to the raw text is
`ea-ansible backup export <host> <id>`, run by someone who already has a shell here.

The text reaches us through a file in the job's private directory written by an internal
playbook (`playbooks/config_backup.yml`, not a catalogue entry, so no agent can name it)
under `no_log`; it never enters a job event or output.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from typing import Any, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from executor import credentials, inventory, redaction
from executor import store as state

ENV_KEEP = "REZZTOR_ANSIBLE_BACKUP_KEEP"
ENV_INTERVAL = "REZZTOR_ANSIBLE_BACKUP_INTERVAL_HOURS"
DEFAULT_KEEP = 30
MAX_BYTES = 5 * 1024 * 1024
HOST_CAP = 10
PLAYBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playbooks", "config_backup.yml")

_ID = re.compile(r"^[0-9a-f]{32}$")
#: Lines a device rewrites without a configuration change, left out of the hash so an
#: unchanged config is not stored again. ponytail: IOS comment lines, unverified on a device.
_VOLATILE = re.compile(r"^! (?:Last configuration change|NVRAM config last updated).*$", re.MULTILINE)


class BackupError(Exception):
    """Refused. The message is written to be shown to an administrator."""


def valid_host(value: Any) -> bool:
    return isinstance(value, str) and bool(inventory.NAME.match(value))


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _keep() -> int:
    try:
        return max(1, int(os.getenv(ENV_KEEP) or DEFAULT_KEEP))
    except ValueError:
        return DEFAULT_KEEP


def _write(path: str, data: bytes) -> None:
    """Beside the target and renamed, mode 0600, so a crash leaves the previous file."""
    temporary = f"{path}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        os.unlink(temporary)
        raise
    os.replace(temporary, path)


class BackupStore:
    def __init__(self, root: str) -> None:
        self.root = root
        self._lock = threading.Lock()

    def _dir(self, host: str) -> str:
        if not valid_host(host):
            raise BackupError(f"'{host}' is not a usable host name.")
        return os.path.join(self.root, host)

    def _index(self, host: str) -> list[dict[str, Any]]:
        try:
            with open(os.path.join(self._dir(host), "index.json"), encoding="utf-8") as handle:
                rows = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as error:
            raise BackupError(f"The backup index for {host} is unreadable: {error}") from error
        return rows if isinstance(rows, list) else []

    def list(self, host: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Metadata only, newest first: id, taken_at, checked_at, sha256, size."""
        try:
            hosts = [host] if host else sorted(os.listdir(self.root))
        except FileNotFoundError:
            return {}
        out = {name: self._index(name) for name in hosts if valid_host(name)}
        return {name: rows for name, rows in out.items() if rows or host}

    def save(self, host: str, text: str) -> dict[str, Any]:
        raw = text.encode("utf-8")
        if not raw.strip():
            raise BackupError("the device returned an empty configuration.")
        if len(raw) > MAX_BYTES:
            raise BackupError(f"the configuration is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        digest = hashlib.sha256(_VOLATILE.sub("", text).encode("utf-8")).hexdigest()
        key = credentials._key()  # fails closed: no key, no backup
        with self._lock:
            os.makedirs(self._dir(host), mode=0o700, exist_ok=True)
            rows = self._index(host)
            if rows and rows[0]["sha256"] == digest:
                rows[0]["checked_at"] = _now()
                self._save_index(host, rows)
                return {**rows[0], "changed": False}
            backup_id = secrets.token_hex(16)
            nonce = secrets.token_bytes(credentials.NONCE_BYTES)
            sealed = AESGCM(key).encrypt(nonce, raw, f"{host}/{backup_id}".encode())
            _write(os.path.join(self._dir(host), f"{backup_id}.enc"), nonce + sealed)
            at = _now()
            row = {"id": backup_id, "taken_at": at, "checked_at": at, "sha256": digest, "size": len(raw)}
            rows.insert(0, row)
            for old in rows[_keep():]:
                try:
                    os.unlink(os.path.join(self._dir(host), f"{old['id']}.enc"))
                except FileNotFoundError:
                    pass
            self._save_index(host, rows[:_keep()])
            return {**row, "changed": True}

    def _save_index(self, host: str, rows: list[dict[str, Any]]) -> None:
        _write(os.path.join(self._dir(host), "index.json"), json.dumps(rows).encode())

    def raw(self, host: str, backup_id: str) -> str:
        """Plaintext. Reachable from the CLI export and from `text`/`diff` below, which redact it."""
        if not valid_id(backup_id) or not any(r["id"] == backup_id for r in self._index(host)):
            raise BackupError("There is no such backup.")
        try:
            with open(os.path.join(self._dir(host), f"{backup_id}.enc"), "rb") as handle:
                blob = handle.read()
            n = credentials.NONCE_BYTES
            return AESGCM(credentials._key()).decrypt(blob[:n], blob[n:], f"{host}/{backup_id}".encode()).decode("utf-8")
        except (OSError, InvalidTag, ValueError) as error:
            raise BackupError("That backup cannot be read with the key this executor is configured with.") from error

    def text(self, host: str, backup_id: str) -> str:
        return redaction.redact(self.raw(host, backup_id))

    def diff(self, host: str, old: str, new: str) -> dict[str, Any]:
        """Diff the raw texts, then redact each line, so a changed secret is a changed line without its value.

        ponytail: key material and keyed secrets (an SNMP community that is a key) are masked
        whole before the diff, so a change to one of those shows no change.
        """
        sides = []
        for i in (old, new):
            text = self.raw(host, i)
            for _name, pattern in redaction.STRUCTURAL_SECRETS:
                text = pattern.sub(redaction.MASK, text)
            text = redaction._mask_keyed_tables(text)  # needs the whole text: a diff line has no table around it
            sides.append(text.splitlines())
        lines = list(difflib.unified_diff(*sides, fromfile=old, tofile=new, lineterm="", n=2))
        changed = sum(line.startswith(("+", "-")) for line in lines[2:])
        # The marker is kept apart: a rule anchored to the start of a line (`key 7 ...`) must not miss `+ key 7 ...`.
        return {"diff": "\n".join(line[:1] + redaction.redact(line[1:]) for line in lines), "changed_lines": changed}


_store: BackupStore | None = None
_store_lock = threading.Lock()
_run_lock = threading.Lock()


def store() -> BackupStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = BackupStore(os.path.join(state.state_dir(), "backups"))
        return _store


def reset_for_tests(path: str | None = None) -> None:
    global _store
    with _store_lock:
        _store = BackupStore(path) if path else None


def take(hosts: list[str], run_playbook: Callable[[str, str, str], int]) -> dict[str, dict[str, Any]]:
    """Back up `hosts` (already validated names) and return one result per host.

    `run_playbook(private_dir, out_dir, limit)` runs the internal playbook and returns its exit
    code; its output is never read. One run at a time.
    """
    known = inventory.store().load()["hosts"]
    results = {h: {"ok": False, "error": "it is not in the executor inventory."} for h in hosts if h not in known}
    todo = [h for h in hosts if h in known]
    if not todo:
        return results
    credentials._key()
    with _run_lock:
        root = os.getenv("REZZTOR_ANSIBLE_RUNDIR", "/tmp/ansible-runs")
        os.makedirs(root, exist_ok=True)
        private = tempfile.mkdtemp(prefix="backup-", dir=root)
        try:
            out = os.path.join(private, "configs")
            os.mkdir(out, 0o700)
            code = run_playbook(private, out, ",".join(todo))
            for host in todo:
                path = os.path.join(out, f"{host}.txt")
                try:
                    if os.path.getsize(path) > MAX_BYTES:
                        raise BackupError(f"the configuration is larger than {MAX_BYTES // (1024 * 1024)} MB.")
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        results[host] = {"ok": True, **store().save(host, handle.read())}
                except FileNotFoundError:
                    results[host] = {"ok": False, "error": f"the device returned no configuration (exit code {code}); is it reachable?"}
                except BackupError as error:
                    results[host] = {"ok": False, "error": str(error)}
        finally:
            shutil.rmtree(private, ignore_errors=True)
    return results


def interval_seconds() -> float:
    try:
        return max(0.0, float(os.getenv(ENV_INTERVAL) or 0)) * 3600
    except ValueError:
        return 0.0


def managed_hosts() -> list[str]:
    return sorted(
        name for name, host in inventory.store().load()["hosts"].items()
        if (host.get("vars") or {}).get("eagent_managed") == "inventory"
    )


def run_all(run_playbook: Callable[[str, str, str], int]) -> dict[str, dict[str, Any]]:
    """Every inventory-managed host, ten at a time."""
    hosts, results = managed_hosts(), {}
    for start in range(0, len(hosts), HOST_CAP):
        results.update(take(hosts[start:start + HOST_CAP], run_playbook))
    return results


def serve_in_background(run_playbook: Callable[[str, str, str], int]) -> None:
    """The timer. Off unless `REZZTOR_ANSIBLE_BACKUP_INTERVAL_HOURS` is set above zero."""
    seconds = interval_seconds()
    if not seconds:
        return

    def loop() -> None:
        while True:
            try:
                run_all(run_playbook)
            except Exception:  # noqa: BLE001 - a failed round must not end the timer
                pass
            time.sleep(seconds)

    threading.Thread(target=loop, daemon=True, name="config-backups").start()
