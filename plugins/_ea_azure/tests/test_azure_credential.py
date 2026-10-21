"""The stored credential: made here or typed once, write-only, and read only by the client."""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from helpers import dotenv, secrets
from plugins._ea_azure.helpers import config, credential
from plugins._ea_azure.tests.fixtures.env import install

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = install(monkeypatch, tmp_path)
    config.save({"tenant_id": "7c1e2a40-0000-4000-8000-3f5b9d1e6a21"})
    return store


def test_nothing_stored_is_reported_as_unset(env):
    assert credential.status() == {"kind": "", "set": False, "fingerprint": "", "expires_on": ""}
    assert credential._material() == ("", "")
    with pytest.raises(ValueError):
        credential.public_certificate()


def test_generate_makes_a_3072_bit_year_long_certificate_named_for_the_tenant(env):
    status = credential.generate_certificate()
    blob = base64.b64decode(env[dotenv.KEY_AZURE_CLIENT_CERT])
    key_pem, cert_pem = blob.split(b"-----BEGIN CERTIFICATE-----", 1)
    cert = x509.load_pem_x509_certificate(b"-----BEGIN CERTIFICATE-----" + cert_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    assert key.key_size == 3072
    assert cert.subject.rfc4514_string() == "CN=rezztor-netops-7c1e2a40"
    assert 364 <= (cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).days <= 365
    assert status["kind"] == "certificate" and status["set"] is True
    assert status["fingerprint"] == cert.fingerprint(hashes.SHA1()).hex().upper()  # what Azure calls the thumbprint
    assert status["expires_on"] == cert.not_valid_after_utc.date().isoformat()
    kind, material = credential._material()
    assert kind == "certificate" and "PRIVATE KEY" in material and "BEGIN CERTIFICATE" in material


def test_the_public_file_holds_no_key(env):
    credential.generate_certificate()
    pem = credential.public_certificate()
    assert b"PRIVATE" not in pem and pem.startswith(b"-----BEGIN CERTIFICATE-----")
    x509.load_pem_x509_certificate(pem)


def test_a_secret_is_stored_and_reported_without_its_value(env):
    status = credential.set_secret("  s3cret-value ", "2027-03-01")
    assert env[dotenv.KEY_AZURE_CLIENT_SECRET] == "s3cret-value"
    assert status == {"kind": "secret", "set": True, "fingerprint": "", "expires_on": "2027-03-01"}
    assert "s3cret-value" not in str(status)
    assert credential._material() == ("secret", "s3cret-value")


def test_an_empty_secret_leaves_what_is_stored_alone(env):
    credential.set_secret("keep-me", "2027-03-01")
    credential.set_secret("", "2030-01-01")
    credential.set_secret("   ", "2030-01-01")
    assert env[dotenv.KEY_AZURE_CLIENT_SECRET] == "keep-me"
    assert credential.status()["expires_on"] == "2027-03-01"


def test_each_kind_replaces_the_other(env):
    credential.set_secret("a-secret", "")
    credential.generate_certificate()
    assert credential.status()["kind"] == "certificate" and not env[dotenv.KEY_AZURE_CLIENT_SECRET]
    credential.set_secret("another", "")
    assert credential.status()["kind"] == "secret" and not env[dotenv.KEY_AZURE_CLIENT_CERT]


def test_clear_removes_both(env):
    credential.generate_certificate()
    credential.clear()
    assert credential.status()["set"] is False


def test_the_keys_are_not_agent_usable_aliases():
    assert not dotenv.KEY_AZURE_CLIENT_SECRET.startswith("API_KEY") and not dotenv.KEY_AZURE_CLIENT_CERT.startswith("API_KEY")
    assert not {dotenv.KEY_AZURE_CLIENT_SECRET, dotenv.KEY_AZURE_CLIENT_CERT} & set(secrets._RUNTIME_CREDENTIAL_KEYS)


def test_only_the_client_reads_the_credential():
    """No module under api/ or tools/ touches `_material` or the two keys; only azure_config.py may call set_secret."""
    for folder in ("api", "tools"):
        for path in (PLUGIN / folder).glob("*.py"):
            source = path.read_text()
            assert "_material" not in source and "KEY_AZURE_CLIENT" not in source, path.name
            if "credential.set_secret" in source:
                assert path.name == "azure_config.py"
    users = [p.name for p in (PLUGIN / "helpers").glob("*.py") if "credential._material" in p.read_text()]
    assert users == ["client.py"]
