"""E1-E6: the architecture contract, written before the features it protects.

These are negative tests. They assert things that must stay false -- no HTTP
client outside the one module allowed to have one, no SSH client anywhere, no
job field through which content or a credential could arrive, and no network
policy quietly loosened. A green run here is not evidence the plugin works; it
is evidence the plugin has not grown a hole.

The plugin's own tree is scanned rather than a list of known modules, so a file
added later is covered without anybody remembering to add it here.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from plugins._ea_ansible.helpers import job_schema

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _plugin_sources() -> list[pathlib.Path]:
    """Every Python file in the plugin except the tests.

    Tests are excluded because they necessarily name the things they forbid.
    """
    return [
        path
        for path in PLUGIN_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(PLUGIN_ROOT).parts
        and "__pycache__" not in path.parts
    ]


# --- E1: two modules reach the executor, and only two ------------------------

HTTP_CLIENTS = ("requests", "httpx", "aiohttp", "urllib.request", "http.client")

#: Named, not counted. `executor_client` carries the job token and runs
#: catalogue entries; `admin_client` carries the administration token and edits
#: the inventory and credentials. The split is the point -- a compromised agent
#: holds only the first -- so a third name here needs an answer to "which token
#: does it carry, and why could neither of these do the job".
EXECUTOR_CLIENTS = ("executor_client.py", "admin_client.py")


def test_e1_only_the_named_clients_speak_http():
    offenders = []
    for path in _plugin_sources():
        if path.name in EXECUTOR_CLIENTS:
            continue
        text = path.read_text()
        for client in HTTP_CLIENTS:
            if re.search(rf"^\s*(import|from)\s+{re.escape(client)}\b", text, re.M):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {client}")
    assert not offenders, (
        "Only helpers/executor_client.py and helpers/admin_client.py may reach "
        "the executor: " + "; ".join(offenders)
    )


def test_e1_the_admin_token_is_not_reachable_from_the_job_client():
    """The two clients read two different keys out of usr/.env. If
    executor_client could read the administration token, the split would be a
    naming convention rather than a boundary."""
    text = (REPO_ROOT / "plugins/_ea_ansible/helpers/executor_client.py").read_text()
    assert "admin_token" not in text
    assert "ADMIN" not in text


# --- E2: nothing in this plugin opens a session to a device ------------------

SSH_CLIENTS = ("paramiko", "asyncssh", "fabric", "netmiko", "scrapli", "pexpect")


def test_e2_no_ssh_client_anywhere_in_the_plugin():
    offenders = []
    for path in _plugin_sources():
        text = path.read_text()
        for client in SSH_CLIENTS:
            if re.search(rf"^\s*(import|from)\s+{re.escape(client)}\b", text, re.M):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {client}")
    assert not offenders, (
        "The agent never opens a session to a device; Ansible is the bridge. "
        + "; ".join(offenders)
    )


def test_e2_no_shell_out_to_ssh():
    offenders = []
    for path in _plugin_sources():
        text = path.read_text()
        if re.search(r"""["'`]\s*ssh\s+""", text) or re.search(r"\bssh\b.*subprocess", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "Shelling out to ssh is the same hole with extra steps: " + "; ".join(offenders)


# --- E3: no job field carries content, a command, or a credential ------------

def test_e3_allowed_and_forbidden_job_fields_do_not_overlap():
    assert not (job_schema.ALLOWED_JOB_FIELDS & job_schema.FORBIDDEN_JOB_FIELDS)


@pytest.mark.parametrize(
    "field",
    ["playbook", "content", "tasks", "command", "shell", "device_ip", "password", "vault_password"],
)
def test_e3_the_dangerous_field_names_are_not_accepted(field):
    assert field not in job_schema.ALLOWED_JOB_FIELDS


def test_e3_a_job_carrying_content_is_refused():
    with pytest.raises(ValueError, match="refuses"):
        job_schema.validate_job_fields(
            {"playbook_name": "reads/device_facts", "playbook": "- hosts: all"}
        )


def test_e3_a_job_carrying_a_credential_is_refused():
    with pytest.raises(ValueError, match="refuses"):
        job_schema.validate_job_fields({"playbook_name": "x", "password": "hunter2"})


def test_e3_an_unknown_field_is_refused_rather_than_ignored():
    with pytest.raises(ValueError, match="Unknown"):
        job_schema.validate_job_fields({"playbook_name": "x", "force": True})


def test_e3_a_well_formed_job_is_accepted():
    job_schema.validate_job_fields(
        {
            "playbook_name": "reads/device_facts",
            "commit": "0" * 40,
            "variables": {"interface": "GigabitEthernet0/0"},
            "limit": "core-switches",
            "check_mode": True,
        }
    )


# --- E4: the runner's isolation is not quietly loosened ----------------------

RFC1918 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")




# --- E5: the application pod has no route to the estate ----------------------









# --- E6: the token is a reference, never a value -----------------------------

def test_e6_tracked_config_names_a_token_key_and_holds_no_token():
    config = (PLUGIN_ROOT / "default_config.yaml").read_text()
    assert "executor_token_key:" in config, "the config must point at the key, not carry it"
    assert "admin_token_key:" in config, "the config must point at the admin key, not carry it"
    assert not re.search(r"^\s*executor_token\s*:", config, re.M), (
        "a token value must never be stored in tracked configuration"
    )
    assert not re.search(r"^\s*admin_token\s*:", config, re.M), (
        "an administration token value must never be stored in tracked configuration"
    )


def test_e6_the_executor_never_returns_its_token():
    """Checked against the API's own responses, not by reading the source."""
    from executor import app as executor_app

    token = "a-token-that-must-not-appear"
    os.environ["REZZTOR_ANSIBLE_EXECUTOR_TOKEN"] = token
    try:
        client = executor_app.app.test_client()
        bodies = [
            client.get("/healthz").get_data(as_text=True),
            client.post("/jobs", json={"playbook_name": "nope"}).get_data(as_text=True),
            client.post(
                "/jobs",
                json={"playbook_name": "reads/ping_localhost", "commit": "wrong"},
                headers={"Authorization": f"Bearer {token}"},
            ).get_data(as_text=True),
            client.get("/jobs/none", headers={"Authorization": f"Bearer {token}"}).get_data(as_text=True),
        ]
    finally:
        os.environ.pop("REZZTOR_ANSIBLE_EXECUTOR_TOKEN", None)
    for body in bodies:
        assert token not in body


def test_e6_token_shaped_names_can_never_become_job_fields():
    for name in ("token", "secret", "credential", "password"):
        assert name in job_schema.FORBIDDEN_JOB_FIELDS


# --- accountable approval, in the role table (ADR 0039) ----------------------

def test_a_power_user_may_ask_for_and_approve_a_change():
    """The requesting engineer is the accountable approver; the user role and
    the agent are not."""
    from helpers import rbac

    power = rbac.ROLES[rbac.ROLE_POWER]["permissions"]
    assert rbac.PERM_TOOL_NETWORK in power
    assert rbac.PERM_NETWORK_APPROVE in power
    assert rbac.PERM_NETWORK_APPROVE in rbac.ROLES[rbac.ROLE_ADMIN]["permissions"]


def test_a_standard_user_reaches_no_network_tool():
    from helpers import rbac

    user = rbac.ROLES[rbac.ROLE_USER]["permissions"]
    assert rbac.PERM_TOOL_NETWORK not in user
    assert rbac.PERM_NETWORK_APPROVE not in user


def test_every_network_tool_requires_the_network_permission():
    from helpers import rbac, tool_permissions

    for tool in ("net_read", "net_change", "net_apply", "net_plan", "net_credential"):
        assert tool_permissions.required_permission(tool) == rbac.PERM_TOOL_NETWORK


def test_approving_is_not_a_tool():
    """The agent drafts; a person approves. There must be no tool through which
    the agent can approve anything, including its own draft."""
    import os

    tools_dir = PLUGIN_ROOT / "tools"
    names = {p.stem for p in tools_dir.glob("*.py")} - {"__init__"}
    assert names == {"net_read", "net_change", "net_apply", "net_plan", "net_credential"}
    for path in tools_dir.glob("*.py"):
        source = path.read_text()
        assert "approved_hash =" not in source, f"{path.name} sets an approval"
        assert "PERM_NETWORK_APPROVE" not in source, "the agent must not name the approve permission"
    # ADR 0077: the approval is a row only `change_store.approve` writes and `change_store.take` uses. No tool of the
    # device or Azure plugins calls the first or writes either status itself.
    for path in (PLUGIN_ROOT.parent).glob("_ea_a*/tools/*.py"):
        source = path.read_text()
        assert "change_store.approve(" not in source, f"{path.name} approves"
        assert "status = APPROVED" not in source and "status = APPLYING" not in source, f"{path.name} writes an approval status"


# --- the runner is not, and does not become, an Ansible control node ---------

def test_e4_the_runner_image_contains_no_ansible():
    """ADR 0030's other half. The runner exists to execute model-authored code;
    an Ansible control plane sharing that filesystem is the arrangement the
    executor exists to avoid, and it is an easy one to reintroduce by habit."""
    dockerfile = (REPO_ROOT / "docker/runner/Dockerfile").read_text().lower()
    for token in ("ansible", "ansible-galaxy", "ansible-playbook"):
        assert token not in dockerfile, f"the runner image installs {token}"


def test_e4_no_device_credentials_are_mounted_into_the_runner():
    """Parsed, not sliced out of the text: the executor's own comments sit
    between the two services and mention an inventory quite legitimately."""
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker/run/docker-compose.yml").read_text())
    runner = compose["services"]["runner"]
    mounts = " ".join(str(v) for v in (runner.get("volumes") or []))
    for token in ("/etc/ansible", "vault.pass", "inventory"):
        assert token not in mounts, f"the runner mounts {token}"

    # And the executor keeps them, which is the other half of the same rule.
    ansible = compose["services"]["ansible"]
    assert "ports" not in ansible
