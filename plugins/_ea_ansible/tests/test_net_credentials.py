"""Device credentials on the executor, and the two tokens that keep them apart.

ADR 0030's shape is that the executor resolves credentials itself, so there is
nothing in the application to leak. That only held while credentials arrived as a
file an operator mounted by hand. Storing them here is what makes the executor a
thing that can be managed -- and it is also the change most able to undo the
property the ADR exists to protect, so the tests are written from the attacker's
side rather than the administrator's.

The two that matter most: there is no path that returns a secret, and the token
the agent holds cannot write the inventory or the credentials that decide which
devices its playbooks reach.
"""

from __future__ import annotations

import base64
import json

import pytest

from executor import credentials
from plugins._ea_ansible.tests.conftest import ADMIN, ADMIN_TOKEN, AUTH, CREDENTIAL_KEY, TOKEN

GOOD = {"kind": "password", "secret": "hunter2-correct-horse", "username": "netops"}


# --- write-only --------------------------------------------------------------

def test_a_stored_secret_never_comes_back(client):
    client.put("/credentials/core-switches", json=GOOD, headers=ADMIN)
    body = client.get("/credentials", headers=ADMIN).get_data(as_text=True)
    assert GOOD["secret"] not in body


def test_the_write_response_does_not_echo_the_secret(client):
    response = client.put("/credentials/core-switches", json=GOOD, headers=ADMIN)
    assert response.status_code == 200
    assert GOOD["secret"] not in response.get_data(as_text=True)


def test_the_listing_reports_a_fingerprint_instead(client):
    """The only question an administrator legitimately has about a stored
    credential is whether the value they are holding is the one that is loaded."""
    client.put("/credentials/core-switches", json=GOOD, headers=ADMIN)
    (record,) = client.get("/credentials", headers=ADMIN).get_json()["credentials"]
    assert record["fingerprint"] == credentials.fingerprint(GOOD["secret"])
    assert record["username"] == "netops"
    assert "secret" not in record and "ciphertext" not in record


def test_a_different_secret_has_a_different_fingerprint(client):
    assert credentials.fingerprint("a") != credentials.fingerprint("b")


# --- the token split ---------------------------------------------------------

def test_the_job_token_cannot_write_a_credential(client):
    """The token the agent holds is the one that travels. A compromised agent
    must be able to run the playbooks it was always allowed to run, and not to
    rewrite what those playbooks reach."""
    assert client.put("/credentials/core", json=GOOD, headers=AUTH).status_code == 401


def test_the_job_token_cannot_list_credentials(client):
    assert client.get("/credentials", headers=AUTH).status_code == 401


def test_the_job_token_cannot_delete_a_credential(client):
    client.put("/credentials/core", json=GOOD, headers=ADMIN)
    assert client.delete("/credentials/core", headers=AUTH).status_code == 401


def test_the_admin_token_cannot_submit_a_job(client):
    """The split runs both ways. Configuration is not execution."""
    job = {
        "playbook_name": "reads/ping_localhost",
        "commit": "unpinned",
        "variables": {},
        "limit": "",
        "check_mode": True,
    }
    assert client.post("/jobs", json=job, headers=ADMIN).status_code == 401


def test_two_identical_tokens_are_refused_rather_than_collapsed(client, monkeypatch):
    """A deployment that sets both to the same value has not made a smaller
    mistake than one that set no admin token. It has made a bigger one that
    looks like it works."""
    monkeypatch.setenv("REZZTOR_ANSIBLE_ADMIN_TOKEN", TOKEN)
    response = client.get("/credentials", headers=AUTH)
    assert response.status_code == 401
    assert "same value" in response.get_json()["error"]


def test_with_no_admin_token_the_configuration_plane_accepts_nothing(client, monkeypatch):
    monkeypatch.delenv("REZZTOR_ANSIBLE_ADMIN_TOKEN", raising=False)
    response = client.get("/credentials", headers=ADMIN)
    assert response.status_code == 401
    assert "no administration token" in response.get_json()["error"]


# --- encryption --------------------------------------------------------------

def test_the_secret_is_not_on_disk_in_the_clear(client, tmp_path):
    client.put("/credentials/core", json=GOOD, headers=ADMIN)
    raw = (tmp_path / "credentials.json").read_text()
    assert GOOD["secret"] not in raw
    assert json.loads(raw)["core"]["ciphertext"]


def test_the_file_is_not_world_readable(client, tmp_path):
    client.put("/credentials/core", json=GOOD, headers=ADMIN)
    mode = (tmp_path / "credentials.json").stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_a_secret_stored_under_another_key_is_refused_not_garbled(tmp_path, monkeypatch):
    monkeypatch.setenv(credentials.ENV_KEY, CREDENTIAL_KEY)
    store = credentials.CredentialStore(str(tmp_path / "c.json"))
    store.put("core", kind="password", secret="hunter2", username="")

    monkeypatch.setenv(credentials.ENV_KEY, base64.b64encode(b"\x01" * 32).decode())
    with pytest.raises(credentials.CredentialError, match="different key"):
        store.resolve("core")


def test_the_name_is_bound_into_the_ciphertext(tmp_path, monkeypatch):
    """Renaming a record in the file must not move a credential onto another
    inventory group. The name is authenticated data, so it cannot be swapped."""
    monkeypatch.setenv(credentials.ENV_KEY, CREDENTIAL_KEY)
    path = tmp_path / "c.json"
    store = credentials.CredentialStore(str(path))
    store.put("core", kind="password", secret="hunter2", username="")

    records = json.loads(path.read_text())
    records["edge"] = records.pop("core")
    path.write_text(json.dumps(records))

    with pytest.raises(credentials.CredentialError):
        store.resolve("edge")


# --- failing closed ----------------------------------------------------------

def test_with_no_key_nothing_is_stored_rather_than_stored_in_the_clear(client, monkeypatch):
    """An executor that quietly kept device passwords unencrypted because a
    variable was unset is the worst outcome here, and the one that looks like it
    is working."""
    monkeypatch.delenv(credentials.ENV_KEY, raising=False)
    response = client.put("/credentials/core", json=GOOD, headers=ADMIN)
    assert response.status_code == 400
    assert "credential key" in response.get_json()["error"]


@pytest.mark.parametrize("key", ["not base64!", base64.b64encode(b"short").decode()])
def test_a_key_that_is_not_32_bytes_is_refused(client, monkeypatch, key):
    monkeypatch.setenv(credentials.ENV_KEY, key)
    assert client.put("/credentials/core", json=GOOD, headers=ADMIN).status_code == 400


# --- ordinary validation -----------------------------------------------------

def test_an_unknown_kind_is_refused(client):
    response = client.put("/credentials/core", json={**GOOD, "kind": "magic"}, headers=ADMIN)
    assert response.status_code == 400


def test_a_credential_with_no_secret_is_refused(client):
    response = client.put("/credentials/core", json={**GOOD, "secret": ""}, headers=ADMIN)
    assert response.status_code == 400


def test_a_name_with_a_path_in_it_is_refused(client):
    assert client.put("/credentials/../etc", json=GOOD, headers=ADMIN).status_code in (400, 404)


def test_deleting_something_that_is_not_there_is_a_miss(client):
    assert client.delete("/credentials/nope", headers=ADMIN).status_code == 404


def test_a_credential_can_be_replaced(client):
    client.put("/credentials/core", json=GOOD, headers=ADMIN)
    client.put("/credentials/core", json={**GOOD, "secret": "new-one"}, headers=ADMIN)
    (record,) = client.get("/credentials", headers=ADMIN).get_json()["credentials"]
    assert record["fingerprint"] == credentials.fingerprint("new-one")
