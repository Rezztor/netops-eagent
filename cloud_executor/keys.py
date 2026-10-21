"""The two Azure identities' key pairs, and the passphrase that encrypts OpenTofu state (ADR 0063, 0064).

`planner` is key B (a second certificate on the read app, Reader), `applier` is key C (the change app). The private
half is made here, never leaves, and is stored encrypted at rest with AES-256-GCM under `REZZTOR_CLOUD_KEY`
(`executor/credentials.py`, copied into the image, does the sealing). The public certificate, thumbprint and expiry
are stored in the clear: they are what an administrator uploads to Entra. There is no call that returns a private key.

`pfx_b64` builds the PKCS#12 bundle in memory for `ARM_CLIENT_CERTIFICATE` and nothing writes it to disk.
`assertion` signs the JWT Entra accepts in place of a secret, for the executor's own ARM calls (`arm.py`).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from executor import credentials, secret_env

ROLES = ("planner", "applier")
ENV_KEY = "REZZTOR_CLOUD_KEY"
ENV_PASSPHRASE = "REZZTOR_CLOUD_STATE_PASSPHRASE"
ENV_STATE_DIR = "REZZTOR_CLOUD_STATE_DIR"
_GUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_lock = threading.RLock()


class KeyRefused(Exception):
    """Refused. The message is written to be shown to an administrator."""


def state_dir() -> str:
    return os.getenv(ENV_STATE_DIR) or "/var/lib/eagent-cloud"


def _paths() -> tuple[str, str]:
    base = state_dir()
    return os.path.join(base, "keys.json"), os.path.join(base, "keys.enc.json")


@contextmanager
def _sealed():
    """The credential store, pointed at this executor's own key variable for as long as it is used."""
    with _lock:
        previous, credentials.ENV_KEY = credentials.ENV_KEY, ENV_KEY
        try:
            yield credentials.CredentialStore(_paths()[1])
        finally:
            credentials.ENV_KEY = previous


def _public() -> dict[str, Any]:
    try:
        with open(_paths()[0], encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise KeyRefused(f"The key record is unreadable: {error}") from error
    return data if isinstance(data, dict) else {}


def _write_public(data: dict[str, Any]) -> None:
    path = _paths()[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def thumbprint(cert: x509.Certificate) -> str:
    """SHA-1 without colons, upper case: the form Entra shows in its Thumbprint column."""
    return cert.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303 - Entra's own identifier, not a security hash


def sealing_key_ok() -> bool:
    """Whether `REZZTOR_CLOUD_KEY` is set and usable (32 random bytes, base64). Reveals nothing about it."""
    with _sealed():
        try:
            credentials._key()  # noqa: SLF001 - asks only whether the sealing key is usable
        except credentials.CredentialError:
            return False
    return True


def passphrase() -> str:
    """The state passphrase P. Refuses an unset or short one: state encrypted with nothing is not encrypted."""
    try:
        value = secret_env.get(ENV_PASSPHRASE)
    except secret_env.SecretConfigError as error:
        raise KeyRefused(str(error)) from error
    if len(value) < 16:
        raise KeyRefused(f"{ENV_PASSPHRASE} must be at least 16 characters; the executor will not plan without it.")
    return value


def make(role: str, tenant_id: str, client_id: str) -> dict[str, Any]:
    """A new RSA 3072 key pair and self-signed certificate (365 days) for `role`, replacing any earlier one."""
    if role not in ROLES:
        raise KeyRefused(f"The role must be one of {', '.join(ROLES)}.")
    if not _GUID.fullmatch(tenant_id or "") or not _GUID.fullmatch(client_id or ""):
        raise KeyRefused("tenant_id and client_id must be the GUIDs Entra shows.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"rezztor-cloud-{role}-{tenant_id[:8]}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    pem_cert = cert.public_bytes(serialization.Encoding.PEM).decode()
    try:
        with _sealed() as store:
            store.put(role, kind="ssh_key", secret=pem_key)
    except credentials.CredentialError as error:
        raise KeyRefused(str(error)) from error
    record = {"tenant_id": tenant_id.lower(), "client_id": client_id.lower(), "certificate": pem_cert,
              "thumbprint": thumbprint(cert), "expires_on": cert.not_valid_after_utc.date().isoformat()}
    with _lock:
        data = _public()
        data[role] = record
        _write_public(data)
    return {"ok": True, "public_certificate": pem_cert, "thumbprint": record["thumbprint"], "expires_on": record["expires_on"]}


def status() -> dict[str, Any]:
    data = _public()
    out: dict[str, Any] = {}
    for role in ROLES:
        r = data.get(role) or {}
        out[role] = {"set": bool(r), "thumbprint": r.get("thumbprint", ""), "expires_on": r.get("expires_on", ""),
                     "client_id": r.get("client_id", "")}
    try:
        out["passphrase_set"] = bool(passphrase())
    except KeyRefused:
        out["passphrase_set"] = False
    return out


def identity(role: str) -> tuple[str, str] | None:
    """`(tenant_id, client_id)` for a role that has a key, else None."""
    r = _public().get(role)
    return (r["tenant_id"], r["client_id"]) if r else None


def _material(role: str) -> tuple[Any, x509.Certificate]:
    r = _public().get(role)
    if not r:
        raise KeyRefused(f"The {role} key has not been made yet.")
    try:
        with _sealed() as store:
            pem = store.resolve(role)
    except credentials.CredentialError as error:
        raise KeyRefused(str(error)) from error
    return (serialization.load_pem_private_key(pem.encode(), None),
            x509.load_pem_x509_certificate(r["certificate"].encode()))


def pfx_b64(role: str) -> str:
    """The key and certificate as a base64 PKCS#12 with no password, for `ARM_CLIENT_CERTIFICATE`. In memory only."""
    key, cert = _material(role)
    blob = pkcs12.serialize_key_and_certificates(b"rezztor", key, cert, None, serialization.NoEncryption())
    return base64.b64encode(blob).decode()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def assertion(role: str, audience: str) -> str:
    """A signed client assertion (RS256, `x5t` set) for `role`, valid ten minutes."""
    key, cert = _material(role)
    tenant, client = identity(role) or ("", "")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "x5t": _b64u(cert.fingerprint(hashes.SHA1()))}  # noqa: S303
    claims = {"aud": audience, "iss": client, "sub": client, "jti": str(uuid.uuid4()), "nbf": now, "exp": now + 600}
    body = f"{_b64u(json.dumps(header).encode())}.{_b64u(json.dumps(claims).encode())}"
    return f"{body}.{_b64u(key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256()))}"
