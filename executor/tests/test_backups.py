"""Config backups: encrypted at rest, deduplicated, and never returned raw over HTTP."""

from __future__ import annotations

import os
import stat

import pytest

from executor import app as executor_app
from executor import backups, cli
from executor import credentials as executor_credentials
from executor import inventory as executor_inventory
from executor import store as executor_store

TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
SECRET = "$1$Zq9xKvLm$HASHOFTHEPASSWORD"
CONFIG = f"hostname sw1\nenable secret 5 {SECRET}\ninterface Gi1/0/1\n shutdown\n"
INVENTORY = {
    "hosts": {
        "sw1": {"ansible_host": "10.1.1.1", "vars": {"eagent_managed": "inventory"}},
        "sw2": {"ansible_host": "10.1.1.2"},
    },
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(executor_app.ENV_TOKEN, TOKEN)
    monkeypatch.setenv(executor_credentials.ENV_KEY, KEY)
    monkeypatch.setenv("REZZTOR_ANSIBLE_RUNDIR", str(tmp_path / "runs"))
    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    executor_credentials.reset_for_tests(str(tmp_path / "credentials.json"))
    executor_inventory.reset_for_tests(str(tmp_path / "inventory.json"))
    executor_inventory.store().save(INVENTORY)
    backups.reset_for_tests(str(tmp_path / "backups"))
    executor_app.app.config.update(TESTING=True)
    try:
        yield executor_app.app.test_client()
    finally:
        for module in (executor_store, executor_credentials, executor_inventory, backups):
            module.reset_for_tests(None)


def device(monkeypatch, texts):
    """Replace the playbook: write `texts[host]` where the real one would, and remember the limit."""
    seen = []

    def run(private_dir, out_dir, limit):
        seen.append(limit)
        for host in limit.split(","):
            if host in texts:
                with open(os.path.join(out_dir, f"{host}.txt"), "w", encoding="utf-8") as file:
                    file.write(texts[host])
        return 0

    monkeypatch.setattr(executor_app, "_run_backup_playbook", run)
    return seen


def post(client, hosts):
    return client.post("/v1/backups", json={"hosts": hosts}, headers=AUTH)


def test_a_second_identical_config_stores_nothing_and_text_is_encrypted_and_redacted(client, monkeypatch, tmp_path):
    device(monkeypatch, {"sw1": CONFIG})
    first = post(client, ["sw1"]).get_json()["results"]["sw1"]
    again = post(client, ["sw1"]).get_json()["results"]["sw1"]
    assert (first["ok"], first["changed"], again["changed"], again["id"]) == (True, True, False, first["id"])
    assert again["size"] == len(CONFIG) and len(again["sha256"]) == 64

    listed = client.get("/v1/backups?host=sw1", headers=AUTH).get_json()["backups"]["sw1"]
    assert [r["id"] for r in listed] == [first["id"]] and listed[0]["checked_at"] >= listed[0]["taken_at"]

    file = tmp_path / "backups" / "sw1" / f"{first['id']}.enc"
    assert SECRET.encode() not in file.read_bytes() and stat.S_IMODE(file.stat().st_mode) == 0o600
    assert not os.listdir(tmp_path / "runs"), "the job's private directory is removed"

    responses = [
        post(client, ["sw1"]), client.get("/v1/backups", headers=AUTH),
        client.get(f"/v1/backups/sw1/{first['id']}", headers=AUTH),
    ]
    assert all(SECRET not in r.get_data(as_text=True) for r in responses)
    text = responses[2].get_json()["text"]
    assert "enable secret 5 [redacted]" in text and "hostname sw1" in text


def test_a_changed_secret_is_a_changed_line_without_its_value(client, monkeypatch):
    texts = {"sw1": CONFIG}
    device(monkeypatch, texts)
    old = post(client, ["sw1"]).get_json()["results"]["sw1"]["id"]
    texts["sw1"] = CONFIG.replace(SECRET, "$1$other$ANOTHERHASH").replace("hostname sw1", "hostname core1")
    new = post(client, ["sw1"]).get_json()["results"]["sw1"]
    assert new["changed"] and new["id"] != old

    out = client.get(f"/v1/backups/sw1/diff?from={old}&to={new['id']}", headers=AUTH)
    body = out.get_json()
    assert body["changed_lines"] == 4
    assert "-hostname sw1" in body["diff"] and "+hostname core1" in body["diff"]
    assert "+enable secret 5 [redacted]" in body["diff"] and "ANOTHERHASH" not in out.get_data(as_text=True)
    assert SECRET not in out.get_data(as_text=True)


def test_a_changed_key_line_is_redacted_behind_its_diff_marker(client, monkeypatch):
    """`key 7 ...` is caught by a rule anchored to the start of a line, which a `+` or `-` used to hide."""
    texts = {"sw1": CONFIG + "tacacs server T1\n key 7 0822455D0A16\n"}
    device(monkeypatch, texts)
    old = post(client, ["sw1"]).get_json()["results"]["sw1"]["id"]
    texts["sw1"] = texts["sw1"].replace("0822455D0A16", "13061E010803")
    new = post(client, ["sw1"]).get_json()["results"]["sw1"]["id"]

    body = client.get(f"/v1/backups/sw1/diff?from={old}&to={new}", headers=AUTH).get_data(as_text=True)
    assert "0822455D0A16" not in body and "13061E010803" not in body
    assert "key 7 [redacted]" in body


def test_json_config_secrets_are_redacted(client, monkeypatch):
    device(monkeypatch, {"sw1": '{"TACPLUS_SERVER": {"1.1.1.1": {"passkey": "hunter2hunter2", "tcp_port": "49"}}}'})
    backup_id = post(client, ["sw1"]).get_json()["results"]["sw1"]["id"]
    text = client.get(f"/v1/backups/sw1/{backup_id}", headers=AUTH).get_json()["text"]
    assert "hunter2" not in text and '"passkey": "[redacted]"' in text and '"tcp_port": "49"' in text


def test_refusals(client, monkeypatch):
    device(monkeypatch, {"sw1": CONFIG})
    assert client.post("/v1/backups", json={"hosts": ["sw1"]}).status_code == 401
    assert client.get("/v1/backups").status_code == 401
    assert post(client, []).status_code == 400
    assert post(client, [f"h{i}" for i in range(11)]).status_code == 400
    assert post(client, ["sw1; rm -rf /"]).status_code == 400
    assert post(client, ["nope"]).get_json()["results"]["nope"]["ok"] is False
    assert post(client, ["sw2"]).get_json()["results"]["sw2"]["ok"] is False  # the device printed nothing
    first = post(client, ["sw1"]).get_json()["results"]["sw1"]["id"]
    assert client.get("/v1/backups/sw1/../../etc", headers=AUTH).status_code in (400, 404)
    assert client.get("/v1/backups/sw1/not-an-id", headers=AUTH).status_code == 400
    assert client.get(f"/v1/backups/sw1/diff?from={first}&to={'0' * 32}", headers=AUTH).status_code == 400


def test_an_id_and_host_are_bound_to_the_ciphertext(client, monkeypatch, tmp_path):
    texts = {"sw1": CONFIG, "sw2": CONFIG}
    device(monkeypatch, texts)
    one = post(client, ["sw1", "sw2"]).get_json()["results"]
    a, b = one["sw1"]["id"], one["sw2"]["id"]
    (tmp_path / "backups" / "sw2" / f"{b}.enc").write_bytes((tmp_path / "backups" / "sw1" / f"{a}.enc").read_bytes())
    assert client.get(f"/v1/backups/sw2/{b}", headers=AUTH).status_code == 400


def test_only_the_newest_few_are_kept(client, monkeypatch, tmp_path):
    monkeypatch.setenv(backups.ENV_KEEP, "2")
    texts = {"sw1": "a"}
    device(monkeypatch, texts)
    for text in ("one\n", "two\n", "three\n"):
        texts["sw1"] = text
        post(client, ["sw1"])
    rows = client.get("/v1/backups?host=sw1", headers=AUTH).get_json()["backups"]["sw1"]
    assert len(rows) == 2 and rows[0]["size"] == len("three\n")
    assert len([n for n in os.listdir(tmp_path / "backups" / "sw1") if n.endswith(".enc")]) == 2


def test_no_key_means_no_backup(client, monkeypatch):
    device(monkeypatch, {"sw1": CONFIG})
    monkeypatch.delenv(executor_credentials.ENV_KEY)
    assert post(client, ["sw1"]).status_code == 503


def test_the_timer_backs_up_managed_hosts_only_and_export_is_the_one_raw_route(client, monkeypatch, capsys):
    seen = device(monkeypatch, {"sw1": CONFIG})
    assert backups.managed_hosts() == ["sw1"]
    results = backups.run_all(executor_app._run_backup_playbook)
    assert list(results) == ["sw1"] and seen == ["sw1"]
    cli.backup(["export", "sw1", results["sw1"]["id"]])
    assert capsys.readouterr().out == CONFIG
    monkeypatch.setenv(backups.ENV_INTERVAL, "0")
    assert backups.interval_seconds() == 0
    monkeypatch.setenv(backups.ENV_INTERVAL, "0.5")
    assert backups.interval_seconds() == 1800
