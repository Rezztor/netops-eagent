"""What the executor refuses to write down -- and what it deliberately keeps.

The interesting half of these tests is the second one. Redacting everything that
looks like a secret would be easy and would quietly break condition 2: an
engineer approves the exact command lines a change would push, and
`snmp-server community ****` cannot be checked against what was intended.

So the split is deliberate and tested from both sides. Key material goes
everywhere it appears, because nobody reviews a private key character by
character and its presence in a command line is itself the finding. Device
passwords and community strings survive in the commands an approver reads, and
do not survive in raw stdout, which is not an approval artifact and is where a
missing `no_log` ends up.
"""

from __future__ import annotations

from executor import redaction

#: Assembled rather than written out. `test_no_private_key_is_committed` scans
#: tracked files for the literal header, and it is right to: a scanner cannot
#: tell a fixture from a real key, and neither can someone skimming a diff. A
#: test that made the repository's own key check noisier would be a bad trade.
_LABEL = " ".join(["OPENSSH", "PRIVATE", "KEY"])
_HEADER = f"{'-' * 5}BEGIN {_LABEL}{'-' * 5}"
_FOOTER = f"{'-' * 5}END {_LABEL}{'-' * 5}"
KEY = f"{_HEADER}\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU\n{_FOOTER}"


# --- removed everywhere ------------------------------------------------------

def test_a_private_key_does_not_survive_even_in_a_command(vault=None):
    assert "BEGIN" not in redaction.redact(KEY, keep_operational=True)
    assert redaction.MASK in redaction.redact(KEY, keep_operational=True)


def test_a_vault_blob_does_not_survive():
    blob = "$ANSIBLE_VAULT;1.1;AES256\n33613463396338383762383838\n3437"
    assert "AES256" not in redaction.redact(blob, keep_operational=True)


def test_a_bearer_token_does_not_survive():
    line = "curl -H 'Authorization: Bearer abcdef0123456789' https://fmc"
    scrubbed = redaction.redact(line, keep_operational=True)
    assert "abcdef0123456789" not in scrubbed


def test_the_executor_token_never_survives(monkeypatch):
    """A token in the output is a bug elsewhere. This is not the place to let it
    through while that is being found."""
    monkeypatch.setenv("REZZTOR_ANSIBLE_EXECUTOR_TOKEN", "s3cr3t-token-value")
    assert "s3cr3t-token-value" not in redaction.redact(
        "auth failed for s3cr3t-token-value", keep_operational=True
    )


def test_a_short_env_value_is_not_used_as_a_needle(monkeypatch):
    """Blanket-replacing a two-character token would corrupt every line that
    happened to contain those two characters."""
    monkeypatch.setenv("REZZTOR_ANSIBLE_EXECUTOR_TOKEN", "ab")
    assert redaction.redact("vlan 40 name fabric") == "vlan 40 name fabric"


# --- kept where an approver has to read them ---------------------------------

def test_a_community_string_survives_in_a_command():
    line = "snmp-server community FINANCE-RO RO"
    assert redaction.redact(line, keep_operational=True) == line


def test_the_same_community_string_does_not_survive_in_raw_output():
    assert "FINANCE-RO" not in redaction.redact("snmp-server community FINANCE-RO RO")


def test_a_device_password_survives_in_a_command():
    line = "username netops password 0 Correct-Horse"
    assert redaction.redact(line, keep_operational=True) == line


def test_a_device_password_does_not_survive_in_raw_output():
    scrubbed = redaction.redact("username netops password 0 Correct-Horse")
    assert "Correct-Horse" not in scrubbed
    # The line still says a password was set. "[redacted]" alone says nothing.
    assert "password" in scrubbed


# --- the shape a job actually produces ---------------------------------------

def test_a_job_result_keeps_its_commands_and_scrubs_its_stdout():
    result = {
        "status": "succeeded",
        "events": [{"task": "snmp", "commands": ["snmp-server community FINANCE-RO RO"]}],
        "output": "ok: changed\nsnmp-server community FINANCE-RO RO\n" + KEY,
        "error": "",
    }
    scrubbed = redaction.redact_job_result(result)
    assert scrubbed["events"][0]["commands"] == ["snmp-server community FINANCE-RO RO"]
    assert "FINANCE-RO" not in scrubbed["output"]
    assert "BEGIN" not in scrubbed["output"]


def test_a_key_in_an_event_is_scrubbed_even_though_events_keep_operational_values():
    result = {"events": [{"commands": [f"key-chain x key-string {KEY}"]}], "output": ""}
    scrubbed = redaction.redact_job_result(result)
    assert "BEGIN" not in scrubbed["events"][0]["commands"][0]


def test_redaction_walks_a_shape_it_has_never_seen():
    """Events are nested dicts whose shape depends on the module. Naming fields
    would mean a module reporting under an unanticipated key slips through
    unredacted, which is exactly the case that matters."""
    weird = {"a": [{"b": {"c": ["padding", KEY]}}]}
    scrubbed = redaction.redact_structure(weird, keep_operational=True)
    assert "BEGIN" not in str(scrubbed)
    assert "padding" in str(scrubbed)


def test_non_strings_come_back_unchanged():
    value = {"rc": 0, "changed": True, "items": [1, 2.5, None]}
    assert redaction.redact_structure(value) == value


# --- the wiring, which is where this could silently not happen ---------------

def test_what_a_job_stores_has_been_through_redaction(client, monkeypatch):
    """The functions above are only worth anything if something calls them."""
    from executor import app as executor_app
    from plugins._ea_ansible.tests.conftest import AUTH, drain

    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {
            "status": "succeeded",
            "exit_code": 0,
            "output": "username netops password 0 Correct-Horse",
            "events": [{"commands": ["username netops password 0 Correct-Horse"]}],
        },
    )
    response = client.post(
        "/jobs",
        json={
            "playbook_name": "reads/ping_localhost",
            "commit": "unpinned",
            "variables": {},
            "limit": "",
            "check_mode": True,
        },
        headers=AUTH,
    )
    drain()
    record = client.get(f"/jobs/{response.get_json()['job_id']}", headers=AUTH).get_json()
    assert "Correct-Horse" not in record["output"]
    assert record["events"][0]["commands"] == ["username netops password 0 Correct-Horse"]


# --- the inventory facts marker ----------------------------------------------

def test_the_inventory_facts_line_survives_redaction():
    """The facts refresh parses `EAGENT_FACTS <host> <json>` out of stdout, as
    the debug callback prints it (quotes escaped), so redaction must not touch it."""
    line = (
        '"msg": "EAGENT_FACTS sw1 {\\"platform\\":\\"ios\\",\\"facts\\":{'
        '\\"ansible_net_hostname\\":\\"sw1\\",\\"ansible_net_serialnum\\":\\"FOC1\\",'
        '\\"ansible_net_version\\":\\"15.2(7)E\\",\\"ansible_net_gather_subset\\":[\\"min\\"]}}"'
    )
    assert redaction.redact(line) == line
    assert redaction.redact(line.replace('\\"', '"')) == line.replace('\\"', '"')
    assert redaction.redact_job_result({"output": line, "events": []})["output"] == line
