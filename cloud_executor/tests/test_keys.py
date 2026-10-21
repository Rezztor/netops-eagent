"""Key pairs: made inside, sealed at rest, never returned, and usable for a PKCS#12 and a signed assertion."""
import base64
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from cloud_executor import keys
from cloud_executor.tests.support import CLIENT, TENANT


def test_status_names_the_certificate_and_never_a_private_key(cloud):
    status = keys.status()
    assert status["planner"]["set"] and status["planner"]["client_id"] == CLIENT
    assert len(status["planner"]["thumbprint"]) == 40 and status["passphrase_set"] is True
    assert "PRIVATE" not in json.dumps(status)


def test_the_private_key_is_sealed_on_disk_and_the_certificate_is_not(cloud):
    sealed = (cloud.state / "keys.enc.json").read_text()
    assert "PRIVATE KEY" not in sealed and "ciphertext" in sealed
    public = (cloud.state / "keys.json").read_text()
    assert "BEGIN CERTIFICATE" in public and "PRIVATE" not in public


def test_a_wrong_sealing_key_cannot_open_the_private_key(cloud, monkeypatch):
    monkeypatch.setenv("REZZTOR_CLOUD_KEY", base64.b64encode(b"x" * 32).decode())
    with pytest.raises(keys.KeyRefused, match="different key"):
        keys.pfx_b64("planner")


def test_no_sealing_key_means_nothing_is_stored(cloud, monkeypatch):
    monkeypatch.delenv("REZZTOR_CLOUD_KEY")
    with pytest.raises(keys.KeyRefused, match="no credential key"):
        keys.make("planner", TENANT, CLIENT)


def test_the_assertion_is_signed_by_the_key_the_certificate_holds(cloud):
    jwt = keys.assertion("planner", "https://login.example/token")
    head, claims, sig = jwt.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    body = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert body["iss"] == body["sub"] == CLIENT and body["aud"] == "https://login.example/token"
    cert = x509.load_pem_x509_certificate((cloud.state / "keys.json").read_text().split('"certificate": "')[1].split('"')[0]
                                          .encode().decode("unicode_escape").encode())
    cert.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.parametrize("role, tenant, client", [("owner", TENANT, CLIENT), ("planner", "nope", CLIENT), ("planner", TENANT, "")])
def test_a_bad_request_for_a_key_is_refused(cloud, role, tenant, client):
    with pytest.raises(keys.KeyRefused):
        keys.make(role, tenant, client)


def test_a_short_or_missing_passphrase_is_refused(cloud, monkeypatch):
    monkeypatch.setenv("REZZTOR_CLOUD_STATE_PASSPHRASE", "short")
    with pytest.raises(keys.KeyRefused):
        keys.passphrase()
    assert keys.status()["passphrase_set"] is False
