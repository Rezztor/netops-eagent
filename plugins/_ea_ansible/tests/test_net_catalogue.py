"""The catalogue, and the schema that keeps a variable from becoming a command.

The shipped catalogue must load; and the loader must refuse the shapes that
would quietly widen what the agent can do -- an unconstrained string, a change
with no way back, a variable nobody declared.
"""

from __future__ import annotations

import os

import pytest
import yaml

from plugins._ea_ansible.helpers import catalogue

PLAYBOOK_DIR = os.path.join(catalogue.PLUGIN_DIR, "playbooks")


def write(tmp_path, document) -> str:
    path = tmp_path / "catalogue.yaml"
    path.write_text(yaml.safe_dump(document))
    return str(path)


# --- the catalogue we actually ship ------------------------------------------

def test_the_shipped_catalogue_loads():
    entries = catalogue.load()
    assert "reads/ping_localhost" in entries


def test_inventory_facts_entry_and_marker_line():
    entry = catalogue.load()["reads/inventory_facts"]
    assert (entry.kind, entry.variables) == ("read", {})
    assert set(entry.platforms) == {"ios", "nxos", "eos", "junos", "sonic", "sonic_community", "fortios", "panos"}
    text = open(catalogue.playbook_path(entry), encoding="utf-8").read()
    assert "EAGENT_FACTS {{ inventory_hostname }}" in text and "gather_subset: min" in text


#: IOS reads go through a module, so the commands are what the module sends
#: and cannot appear in the playbook; the module name must.
MODULE_BASED = {"ios": "cisco.ios.ios_"}


def test_every_read_command_appears_in_its_playbook():
    for entry in catalogue.load().values():
        if entry.kind != "read":
            continue
        text = open(catalogue.playbook_path(entry), encoding="utf-8").read()
        for platform, commands in entry.read_commands.items():
            if platform in MODULE_BASED:
                assert MODULE_BASED[platform] in text, (entry.name, platform)
                continue
            for command in commands:
                if command[1:-1] in entry.variables:  # `<variable>`: the playbook must use that variable
                    command = command[1:-1]
                assert command in text, f"{entry.name}/{platform}: {command!r} is not in the playbook"


SHOW_OK = (
    "show vlan brief", "show ip route 10.1.0.0/24", "show ipv6 route 2001:db8::/32", "show running-config",
    "show interfaces Ethernet0", "show ip bgp neighbors 10.0.0.1 advertised-routes", "show mac address-table vlan 10",
)
SHOW_REFUSED = (
    "show", "show ", "Show vlan", "config terminal", "sudo show vlan", "show  vlan", "show vlan ",
    "show vlan | include 10", "show vlan; reboot", "show vlan && id", "show $(id)", "show `id`", "show vlan > /tmp/x",
    "show 'x'", "show {{x}}", "show a\\b", "show vlan\n", "show vlan\nreboot", "show vlan\treboot",
    "show logging -f", "show vlan --help", "show tech-support", "show techsupport", "show tec", "show TECH-support", "show Techsupport", "show file tftp://1.2.3.4/x",
    "show " + "a " * 13, "show " + "a" * 170,
)


def test_a_free_form_read_takes_plain_show_commands_and_nothing_else():
    """The pattern is the whole reason `reads/show` is read-only (ADR 0046)."""
    entry = catalogue.load()["reads/show"]
    for command in SHOW_OK:
        assert entry.validate_variables({"command_1": command}) == {"command_1": command}
    for command in SHOW_REFUSED:
        with pytest.raises(catalogue.VariableRejected):
            entry.validate_variables({"command_1": command})
        with pytest.raises(catalogue.VariableRejected):  # every slot has the same rule
            entry.validate_variables({"command_1": "show version", "command_5": command})
    with pytest.raises(catalogue.VariableRejected):  # at least one, at most five
        entry.validate_variables({})
    with pytest.raises(catalogue.VariableRejected):
        entry.validate_variables({"command_1": "show version", "command_6": "show vlan brief"})


def test_a_read_must_list_commands_for_every_platform(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "reads/x", "playbook": "playbooks/reads/ping_localhost.yml", "kind": "read",
        "check_mode": "supported", "platforms": ["ios", "eos"], "read_commands": {"ios": ["show version"]},
    }]})
    with pytest.raises(catalogue.CatalogueError, match="read_commands"):
        catalogue.load(path)


def test_every_shipped_entry_has_a_playbook_on_disk():
    for entry in catalogue.load().values():
        assert os.path.exists(catalogue.playbook_path(entry)), entry.name


def test_no_playbook_templates_its_own_host_pattern():
    """The bug that stopped the first attempt dead.

    Every play used `hosts: "{{ eagent_limit }}"` and nothing ever defined that
    variable, so every play failed on an undefined variable. A playbook targets
    `hosts: all` (or localhost) and is scoped by the job's --limit.
    """
    for root, _dirs, names in os.walk(PLAYBOOK_DIR):
        for name in names:
            if not name.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(root, name)
            for play in yaml.safe_load(open(path, encoding="utf-8")) or []:
                hosts = str(play.get("hosts", ""))
                assert "{{" not in hosts, f"{path} templates its host pattern: {hosts!r}"


# --- what the loader refuses -------------------------------------------------

def test_a_string_variable_must_declare_what_it_accepts(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "reads/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "read", "check_mode": "supported",
        "variables": {"target": {"type": "string"}},
    }]})
    with pytest.raises(catalogue.CatalogueError, match="no pattern"):
        catalogue.load(path)


def test_a_change_without_a_rollback_is_refused(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "changes/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "change", "check_mode": "supported",
    }]})
    with pytest.raises(catalogue.CatalogueError, match="rollback"):
        catalogue.load(path)


def test_a_rollback_that_is_not_in_the_catalogue_is_refused(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "changes/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "change", "check_mode": "supported", "rollback": "rollback/nope",
    }]})
    with pytest.raises(catalogue.CatalogueError, match="not in the catalogue"):
        catalogue.load(path)


def test_a_playbook_path_cannot_escape_the_catalogue(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "reads/x", "playbook": "../../../etc/passwd",
        "kind": "read", "check_mode": "supported",
    }]})
    with pytest.raises(catalogue.CatalogueError, match="unusable playbook path"):
        catalogue.load(path)


def test_an_unknown_entry_is_a_miss_not_an_improvisation():
    with pytest.raises(catalogue.UnknownEntry, match="not in the playbook catalogue"):
        catalogue.resolve("reads/whatever_i_imagined")


# --- variable coercion -------------------------------------------------------

def entry_with(**variables) -> catalogue.Entry:
    return catalogue.Entry(
        name="changes/t", playbook="playbooks/reads/ping_localhost.yml",
        kind="read", summary="", check_mode="supported",
        variables={k: catalogue.Variable(name=k, **v) for k, v in variables.items()},
    )


def test_an_undeclared_variable_is_refused_rather_than_dropped():
    entry = entry_with()
    with pytest.raises(catalogue.VariableRejected, match="does not take"):
        entry.validate_variables({"interface": "Gi0/0"})


def test_a_required_variable_must_be_supplied():
    entry = entry_with(iface={"type": "string", "required": True, "pattern": r"^\w+$"})
    with pytest.raises(catalogue.VariableRejected, match="required"):
        entry.validate_variables({})


def test_a_string_outside_its_pattern_is_refused():
    entry = entry_with(iface={"type": "string", "pattern": r"^[A-Za-z0-9/.-]+$"})
    entry.validate_variables({"iface": "GigabitEthernet0/0"})
    for hostile in ("Gi0/0\nreload", "Gi0/0 | sh", "{{ lookup('file','/etc/passwd') }}"):
        with pytest.raises(catalogue.VariableRejected, match="does not accept"):
            entry.validate_variables({"iface": hostile})


def test_an_integer_is_bounded_and_a_bool_is_not_an_integer():
    entry = entry_with(vlan={"type": "integer", "minimum": 1, "maximum": 4094})
    assert entry.validate_variables({"vlan": 100})["vlan"] == 100
    for bad in (0, 4095, True, "100"):
        with pytest.raises(catalogue.VariableRejected):
            entry.validate_variables({"vlan": bad})


def test_a_choice_accepts_only_its_choices():
    entry = entry_with(state={"type": "choice", "choices": ("up", "down")})
    assert entry.validate_variables({"state": "up"})["state"] == "up"
    with pytest.raises(catalogue.VariableRejected, match="must be one of"):
        entry.validate_variables({"state": "sideways"})


# --- where the approved commands come from -----------------------------------

def test_an_entry_that_cannot_report_its_commands_must_carry_reviewed_cli(tmp_path):
    """There is no general offline renderer, so this is the only fallback.

    Verified against the built image: cisco.ios resource modules refuse
    `connection: local` even for `state: rendered`, and enterprise_sonic does
    not accept `rendered` at all. An entry whose modules cannot report what they
    would do has to carry CLI a person reviewed.
    """
    path = write(tmp_path, {"entries": [{
        "name": "changes/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "change", "check_mode": "unsupported", "rollback": "rollback/x",
    }, {
        "name": "rollback/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "rollback", "check_mode": "unsupported", "reviewed_cli": ["no vlan 300"],
    }]})
    with pytest.raises(catalogue.CatalogueError, match="reviewed_cli"):
        catalogue.load(path)


def test_a_check_mode_entry_may_not_also_carry_static_cli(tmp_path):
    """Two sources for the same commands is one source that goes stale."""
    path = write(tmp_path, {"entries": [{
        "name": "reads/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "read", "check_mode": "supported", "reviewed_cli": ["show version"],
    }]})
    with pytest.raises(catalogue.CatalogueError, match="go stale"):
        catalogue.load(path)


def test_reviewed_cli_is_carried_through(tmp_path):
    path = write(tmp_path, {"entries": [{
        "name": "reads/x", "playbook": "playbooks/reads/ping_localhost.yml",
        "kind": "read", "check_mode": "unsupported", "reviewed_cli": ["show version"],
    }]})
    assert catalogue.load(path)["reads/x"].reviewed_cli == ("show version",)


# --- the change entries, and the rules a reviewer holds them to --------------
#
# These are the first entries in the catalogue that push configuration, so the
# tests are about the properties that make a change entry reviewable rather than
# about any one of them working. None has met a device; what can be checked here
# is the shape, and the shape is most of what a reviewer is checking too.

CHANGES = [entry for entry in catalogue.load().values() if entry.kind == "change"]
ROLLBACKS = [entry for entry in catalogue.load().values() if entry.kind == "rollback"]


def test_there_are_change_entries_at_all():
    """For a long time there were none, and every part of the gate was therefore
    untested against anything real. If this ever goes back to zero, the whole
    approval pipeline has nothing to operate on and nothing else would say so."""
    assert CHANGES, "the catalogue has no change entries"


@pytest.mark.parametrize("entry", CHANGES, ids=lambda e: e.name)
def test_a_change_names_a_rollback_that_exists(entry):
    entries = catalogue.load()
    assert entry.rollback in entries


@pytest.mark.parametrize("entry", ROLLBACKS, ids=lambda e: e.name)
def test_a_rollback_carries_literal_cli(entry):
    """Not a gap in the design. A rollback's commands cannot be computed against
    the device, because the device has not had the change applied yet -- asking
    the module what the undo would push reports that nothing needs doing. So the
    approver reads reviewed CLI, and `net_change` reads it from here."""
    assert entry.check_mode == "unsupported"
    assert entry.reviewed_cli


@pytest.mark.parametrize("entry", CHANGES + ROLLBACKS, ids=lambda e: e.name)
def test_no_change_playbook_uses_a_state_that_reverts_what_it_was_not_asked_about(entry):
    """`replaced` and `overridden` rewrite the whole resource, so an entry that
    sets a description would silently drop an unrelated setting nobody mentioned.
    The approval view would not show it either: the module reports what it
    changed, and what it reverted was never in the plan anyone read."""
    with open(catalogue.playbook_path(entry), encoding="utf-8") as handle:
        text = handle.read()
    for state in ("state: replaced", "state: overridden"):
        assert state not in text, f"{entry.name} uses {state}"


#: What a value must never be able to carry into a config line or a template.
#: A catalogue variable is the one place a model-supplied string reaches a
#: device, and ADR 0030's answer is that every one of them is typed and bounded.
INJECTION = [
    "vlan 1 ! shutdown",
    "x {{ lookup('file', '/etc/passwd') }}",
    "x $(id)",
    "x `id`",
    'x " ; reload',
    "x\nno shutdown",
    "x | include password",
    "x' OR '1'='1",
]


#: Only entries that actually take a string. The VLAN entries take integers, and
#: a skip per hostile string per entry would bury the real coverage in noise.
TAKES_A_STRING = [
    entry
    for entry in CHANGES + ROLLBACKS
    if any(variable.type == "string" for variable in entry.variables.values())
]


@pytest.mark.parametrize("entry", TAKES_A_STRING, ids=lambda e: e.name)
@pytest.mark.parametrize("hostile", INJECTION)
def test_no_change_variable_accepts_something_that_means_something_elsewhere(entry, hostile):
    """Every string variable is a pattern, and this is what the patterns are for.

    Values are also wrapped as AnsibleUnsafeText before a play sees them, so this
    is the second of two defences rather than the only one. It is worth testing
    separately because the wrapping stops templating and the pattern is what
    stops a config line."""
    supplied = {
        name: (hostile if variable.type == "string" else 1)
        for name, variable in entry.variables.items()
    }
    with pytest.raises(catalogue.VariableRejected):
        entry.validate_variables(supplied)


@pytest.mark.parametrize("entry", CHANGES, ids=lambda e: e.name)
def test_a_change_says_which_platforms_it_is_for(entry):
    assert entry.platforms and "any" not in entry.platforms, (
        "a change that claims every platform is one nobody reviewed per platform"
    )
