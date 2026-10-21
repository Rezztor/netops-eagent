"""The one credential this plugin signs in with: a certificate the product makes, or a client secret.

Stored in `usr/.env` (never in the plugin's config, never an `API_KEY_*` name, so the agent cannot use it as a
`§§secret()` alias). Only `client.token()` reads it, through `_material()`; everything else sees `status()`.
A certificate's stored form is base64 of the PEM private key followed by the PEM certificate, on one line.
Exactly one credential exists at a time: storing either kind removes the other.

The log app (key D, ADR 0063) is a second, certificate-only identity for Log Analytics under its own name,
`KEY_AZURE_LOGS_CERT`, kept in the same form. It is separate on purpose: it holds Log Analytics Reader and nothing
else, and the read credential above never signs a log query. Only `logs._token()` reads it, through `_logs_material()`.
"""

from __future__ import annotations

import base64
import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from helpers import dotenv
from plugins._ea_azure.helpers import config

_CERT_MARK = b"-----BEGIN CERTIFICATE-----"
# Not `API_KEY_*` and not in `secrets._RUNTIME_CREDENTIAL_KEYS`, like the read credential's names in `helpers/dotenv.py`.
KEY_AZURE_LOGS_CERT = "REZZTOR_AZURE_LOGS_CERT"


def _blob(key: str = dotenv.KEY_AZURE_CLIENT_CERT) -> bytes:
    stored = dotenv.get_dotenv_value(key) or ""
    try:
        return base64.b64decode(stored) if stored else b""
    except ValueError:
        return b""


def _certificate(key: str = dotenv.KEY_AZURE_CLIENT_CERT) -> x509.Certificate | None:
    blob = _blob(key)
    at = blob.find(_CERT_MARK)
    if at < 0:
        return None
    try:
        return x509.load_pem_x509_certificate(blob[at:])
    except ValueError:
        return None


def _cert_status(cert: x509.Certificate) -> dict:
    return {
        "kind": "certificate", "set": True,
        # SHA-1 without colons: the form Azure shows in its "Thumbprint" column, so the two can be compared by eye
        "fingerprint": cert.fingerprint(hashes.SHA1()).hex().upper(),
        "expires_on": cert.not_valid_after_utc.date().isoformat(),
    }


def status() -> dict:
    cert = _certificate()
    if cert is not None:
        return _cert_status(cert)
    if dotenv.get_dotenv_value(dotenv.KEY_AZURE_CLIENT_SECRET):
        return {"kind": "secret", "set": True, "fingerprint": "", "expires_on": config.get_config()["secret_expires_on"]}
    return {"kind": "", "set": False, "fingerprint": "", "expires_on": ""}


def _new_blob(prefix: str) -> str:
    """A new RSA 3072 self-signed certificate, valid 365 days, in the stored form (key then certificate, base64)."""
    tenant = config.get_config()["tenant_id"]
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{prefix}-{tenant[:8]}" if tenant else prefix)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return base64.b64encode(pem_key + cert.public_bytes(serialization.Encoding.PEM)).decode()


def generate_certificate() -> dict:
    """A new certificate for the read app. Replaces any stored credential."""
    dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_CERT, _new_blob("rezztor-netops"))
    dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_SECRET, "")
    return status()


def public_certificate() -> bytes:
    """The PEM certificate only: the file the administrator uploads to the app registration."""
    cert = _certificate()
    if cert is None:
        raise ValueError("No certificate has been created yet.")
    return cert.public_bytes(serialization.Encoding.PEM)


def set_secret(value: str, expires_on: str) -> dict:
    """Store a client secret. An empty value leaves whatever is stored untouched."""
    value = (value or "").strip()
    if value:
        dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_SECRET, value)
        dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_CERT, "")
        config.save({"secret_expires_on": expires_on or ""})
    return status()


def clear() -> None:
    dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_SECRET, "")
    dotenv.save_dotenv_value(dotenv.KEY_AZURE_CLIENT_CERT, "")


def _material() -> tuple[str, str]:
    """`("certificate", <PEM key then PEM certificate>)`, `("secret", <value>)` or `("", "")`. For `client.token()` only."""
    blob = _blob()
    if _certificate() is not None:
        return "certificate", blob.decode()
    secret = dotenv.get_dotenv_value(dotenv.KEY_AZURE_CLIENT_SECRET) or ""
    return ("secret", secret) if secret else ("", "")


# --- the log app (key D) -----------------------------------------------------------------------------------------------

def logs_status() -> dict:
    cert = _certificate(KEY_AZURE_LOGS_CERT)
    return {k: v for k, v in _cert_status(cert).items() if k != "kind"} if cert is not None else {"set": False, "fingerprint": "", "expires_on": ""}


def generate_logs_certificate() -> dict:
    """A new certificate for the log app. Never touches the read credential."""
    dotenv.save_dotenv_value(KEY_AZURE_LOGS_CERT, _new_blob("rezztor-netops-logs"))
    return logs_status()


def public_logs_certificate() -> bytes:
    cert = _certificate(KEY_AZURE_LOGS_CERT)
    if cert is None:
        raise ValueError("No log certificate has been created yet.")
    return cert.public_bytes(serialization.Encoding.PEM)


def clear_logs() -> None:
    dotenv.save_dotenv_value(KEY_AZURE_LOGS_CERT, "")


def _logs_material() -> str:
    """The log app's PEM key then PEM certificate, or "". For `logs._token()` only."""
    return _blob(KEY_AZURE_LOGS_CERT).decode() if _certificate(KEY_AZURE_LOGS_CERT) is not None else ""
