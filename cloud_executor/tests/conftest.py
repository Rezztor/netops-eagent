"""Fixtures for the cloud executor's tests: a private state directory, both keys made, and a fake `tofu`."""
import base64
import json
import shutil
import types

import pytest

from cloud_executor.tests.support import CLIENT, HERE, TENANT, plan_fixture


SEALING_KEY = base64.b64encode(b"k" * 32).decode()


@pytest.fixture(scope="session")
def made_keys(tmp_path_factory):
    """Both key pairs, made once (RSA 3072 is slow) and copied into each test's state directory."""
    from cloud_executor import keys

    folder = tmp_path_factory.mktemp("made-keys")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("REZZTOR_CLOUD_STATE_DIR", str(folder))
        patch.setenv("REZZTOR_CLOUD_KEY", SEALING_KEY)
        keys.make("planner", TENANT, CLIENT)
        keys.make("applier", TENANT, "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee")
    return folder


@pytest.fixture
def cloud(tmp_path, monkeypatch, made_keys):
    """The executor configured as the compose file configures it, with `tofu` replaced by `fake_tofu.py`."""
    from cloud_executor import runner, store

    state, runs, bin_dir = tmp_path / "state", tmp_path / "runs", tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "tofu"
    shutil.copy(HERE / "fake_tofu.py", fake)
    fake.chmod(0o755)
    config = {"plan": plan_fixture(), "state": {}, "fail": {}, "detailed_exitcode": 0}
    (bin_dir / "tofu.json").write_text(json.dumps(config))
    for name, value in {
        "REZZTOR_CLOUD_STATE_DIR": str(state), "REZZTOR_CLOUD_RUN_DIR": str(runs), "REZZTOR_CLOUD_TOFU": str(fake),
        "REZZTOR_CLOUD_KEY": SEALING_KEY, "REZZTOR_CLOUD_STATE_PASSPHRASE": "p" * 24,
        "REZZTOR_DB_CLOUD_PASSWORD": "db-secret-password", "REZZTOR_CLOUD_JOB_TOKEN": "job-token",
        "REZZTOR_CLOUD_ADMIN_TOKEN": "admin-token",
    }.items():
        monkeypatch.setenv(name, value)
    for var in ("REZZTOR_CLOUD_KEY_FILE", "REZZTOR_CLOUD_STATE_PASSPHRASE_FILE", "REZZTOR_CLOUD_JOB_TOKEN_FILE"):
        monkeypatch.delenv(var, raising=False)
    shutil.copytree(made_keys, state)
    applied = []
    monkeypatch.setattr(runner, "existing_ids", lambda sub, group, extra: {str(i).lower() for i in extra})

    def record(**kwargs):
        if kwargs["change_id"] in {a["change_id"] for a in applied}:
            raise store.AlreadyApplied("already")
        applied.append(kwargs)

    monkeypatch.setattr(store, "record_apply", record)
    # the database half of `store.made_groups` (the ledger and the rule are the real ones, in the test's state directory)
    monkeypatch.setattr(store, "_made_rows", lambda sub, makers: [
        (a["workspace"], a["change_id"]) for a in applied
        if a["workspace"].split("/")[0] == sub.lower() and a["workspace"].split("/")[2] in makers])
    monkeypatch.setattr(store, "applied_before", lambda change_id: change_id in {a["change_id"] for a in applied})

    def calls():
        log = bin_dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def script(**changes):
        config.update(changes)
        (bin_dir / "tofu.json").write_text(json.dumps(config))

    return types.SimpleNamespace(applied=applied, calls=calls, script=script, runs=runs, state=state)
