"""Device credentials, encrypted at rest, and never readable back.

ADR 0030's shape is that the executor resolves credentials itself: the
application sends an inventory selector and never a device address, a username or
a password, so there is nothing in the application to leak. That only holds if
the executor is somewhere credentials can actually live -- which until now it was
not, because the only way in was a read-only file an operator mounted by hand.

Three properties, in the order they matter:

**Write-only.** There is no code path that returns a stored secret over HTTP.
Not for an administrator, not for the agent, not for debugging. A store that can
be read back is a store one bug away from being a distribution channel, and an
operator who has lost a device password should rotate it rather than ask us.

**Encrypted with a key that is not in the store.** AES-GCM under a key supplied
by the environment, which in the compose stack is an env
var. Someone who copies the volume gets ciphertext.

**Fails closed.** No key configured means the store refuses to accept anything,
rather than accepting it in the clear. An executor that quietly stored device
passwords unencrypted because a variable was unset is the worst outcome here,
and it is the one that looks like it is working.

A fingerprint is exposed instead of the secret: enough to tell whether the value
you are holding is the value that is loaded, and not enough to be the value.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from executor import secret_env
from executor import store as state

ENV_KEY = "REZZTOR_ANSIBLE_CREDENTIAL_KEY"

#: AES-256. Generate with: head -c32 /dev/urandom | base64
KEY_BYTES = 32
NONCE_BYTES = 12

#: Long enough to distinguish credentials in a list, short enough that it is not
#: worth attacking. It identifies a value; it does not verify one.
FINGERPRINT_CHARS = 16

#: What a credential is for. Named rather than free-form so an inventory group
#: referring to one can be checked against how it will actually be used.
KINDS = ("password", "ssh_key", "api_token", "enable")


class CredentialError(Exception):
    """Refused. The message is written to be shown to an administrator."""


def _key() -> bytes:
    try:
        raw = secret_env.get(ENV_KEY)
    except secret_env.SecretConfigError as error:
        raise CredentialError(str(error)) from error
    if not raw:
        raise CredentialError(
            "This executor has no credential key configured, so it will not store "
            f"a secret at all. Set {ENV_KEY} (or {ENV_KEY}_FILE) to 32 random bytes, base64-encoded."
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as error:  # noqa: BLE001 - any decode failure is the same answer
        raise CredentialError(f"{ENV_KEY} is not valid base64.") from error
    if len(key) != KEY_BYTES:
        raise CredentialError(
            f"{ENV_KEY} must decode to exactly {KEY_BYTES} bytes; this one is {len(key)}."
        )
    return key


def fingerprint(secret: str) -> str:
    """Identifies a value without being one.

    Salted with a fixed label rather than unsalted, so a fingerprint here cannot
    be looked up in a table of hashes of common passwords.
    """
    digest = hashlib.sha256(b"eagent-credential\x00" + secret.encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_CHARS]


class CredentialStore:
    """A JSON file of ciphertext, replaced atomically, mode 0600.

    Not a table in the job database: the job store is read by everything that
    lists history, and a credential has no business sharing a connection with it.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # --- reading, which never means reading a secret -------------------------

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise CredentialError(f"The credential store is unreadable: {error}") from error
        return data if isinstance(data, dict) else {}

    def list(self) -> list[dict[str, Any]]:
        """Names, kinds and fingerprints. Never a secret, by construction: the
        ciphertext is not in the returned shape at all, so a future caller
        cannot accidentally serialise it."""
        records = self._load()
        return [
            {
                "name": name,
                "kind": str(record.get("kind") or ""),
                "username": str(record.get("username") or ""),
                "fingerprint": str(record.get("fingerprint") or ""),
                "updated_at": record.get("updated_at"),
            }
            for name, record in sorted(records.items())
        ]

    def names(self) -> set[str]:
        return set(self._load())

    # --- writing -------------------------------------------------------------

    def put(self, name: str, *, kind: str, secret: str, username: str = "") -> dict[str, Any]:
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise CredentialError(
                "A credential name may contain letters, digits, hyphens and underscores."
            )
        if kind not in KINDS:
            raise CredentialError(f"Unknown credential kind '{kind}'. One of: {', '.join(KINDS)}.")
        if not secret:
            raise CredentialError("A credential with no secret is not a credential.")

        key = _key()
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), name.encode("utf-8"))

        record = {
            "kind": kind,
            "username": username,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "fingerprint": fingerprint(secret),
            "updated_at": time.time(),
        }
        with self._lock:
            records = self._load()
            records[name] = record
            self._write(records)
        return {
            "name": name,
            "kind": kind,
            "username": username,
            "fingerprint": record["fingerprint"],
            "updated_at": record["updated_at"],
        }

    def delete(self, name: str) -> bool:
        with self._lock:
            records = self._load()
            if name not in records:
                return False
            del records[name]
            self._write(records)
        return True

    def _write(self, records: dict[str, Any]) -> None:
        """Caller holds the lock. Written beside the target and renamed, so a
        crash mid-write leaves the previous file rather than half of a new one."""
        temporary = f"{self.path}.tmp"
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(records, file)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            os.unlink(temporary)
            raise
        os.replace(temporary, self.path)

    # --- the one path that decrypts ------------------------------------------

    def resolve(self, name: str) -> str:
        """Plaintext, for building a job's inventory and nothing else.

        Deliberately not reachable from any HTTP handler. If you are adding a
        caller for this, the question to answer first is where the plaintext
        goes next.
        """
        record = self._load().get(name)
        if not record:
            raise CredentialError(f"No credential named '{name}' is stored here.")
        try:
            plaintext = AESGCM(_key()).decrypt(
                base64.b64decode(record["nonce"]),
                base64.b64decode(record["ciphertext"]),
                name.encode("utf-8"),
            )
        except (InvalidTag, KeyError, ValueError) as error:
            raise CredentialError(
                f"'{name}' cannot be decrypted with the key this executor is "
                "configured with. It was stored under a different key."
            ) from error
        return plaintext.decode("utf-8")


_store: CredentialStore | None = None
_store_lock = threading.Lock()


def store() -> CredentialStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CredentialStore(os.path.join(state.state_dir(), "credentials.json"))
        return _store


def reset_for_tests(path: str | None = None) -> CredentialStore | None:
    global _store
    with _store_lock:
        _store = CredentialStore(path) if path else None
        return _store
