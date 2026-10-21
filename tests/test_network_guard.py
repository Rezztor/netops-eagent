"""The network guard: what the generator refuses, and what its rules really do.

Two halves. The first is the generator alone (`deploy/customer/rezztor-guard`):
what it refuses, and which rules each kind of network list produces. The second
loads the generated rules into private network namespaces and tries the
connections, for three VLANs on one trunk and for one flat network, and again
after a simulated Docker restart (`tests/network_guard_proof.sh`, from research
04, appendix A). It needs no root and no Docker: `unshare -Urn` gives the test
its own network stack.

What the second half does NOT prove: the "Docker" in it is a stand-in that
forwards everything. It shows the guard is enough when Docker's own rules do
nothing; it does not show the guard beside a real Docker 29 on a real host.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "customer" / "rezztor-guard"
PROOF = ROOT / "tests" / "network_guard_proof.sh"


def _load():
    loader = importlib.machinery.SourceFileLoader("rezztor_guard", str(TOOL))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("rezztor_guard", loader))
    loader.exec_module(module)
    return module


guard = _load()


def net(name, roles, interface, address, routed=(), gateway=None):
    subnet = str(guard.ipaddress.IPv4Interface(address).network)
    return {"name": name, "roles": list(roles), "interface": interface, "parent": "eno1", "vlan": None,
            "address": address, "gateway": gateway, "subnet": subnet, "routed_subnets": list(routed), "state": "up"}


def listing(*networks):
    return {"version": 1, "generated_at": 1759700000, "networks": list(networks)}


# The owner's scenario: VLAN 100 is layer 2 only, VLAN 200 has device subnets behind its gateway (one of them a
# public prefix), VLAN 300 is the internet and the users.
THREE_VLANS = listing(
    net("VLAN 100", ["devices"], "vlan100", "10.0.100.5/24"),
    net("VLAN 200", ["devices"], "vlan200", "10.0.200.5/24", ["10.20.0.0/16", "198.51.100.0/24"], "10.0.200.1"),
    net("VLAN 300", ["internet", "users"], "vlan300", "10.0.30.5/24", gateway="10.0.30.1"),
)
FULL = {"form": "full", "app_services": [{"address": "10.20.0.9", "protocol": "tcp", "port": 17774}]}
# The same, but the network of the internet and the users also carries devices (a combined network).
THREE_COMBINED = listing(*THREE_VLANS["networks"][:2],
                         net("VLAN 300", ["internet", "users", "devices"], "vlan300", "10.0.30.5/24", gateway="10.0.30.1"))
# One flat network written into a list: it is everything at once.
FLAT_LISTED = listing(net("LAN", ["internet", "users", "devices"], "lan0", "192.168.50.5/24", gateway="192.168.50.1"))


def rules(doc=None, conf=None, dns=(), ssh=False):
    conf = guard.check_conf(conf)
    return guard.rules(guard.check_networks(doc, conf["container_block"]), conf, [guard.ipaddress.IPv4Address(a) for a in dns], ssh)


def chain(text, name):
    return re.search(rf"  chain {name} \{{\n(.*?)\n  \}}", text, re.S).group(1)


# --- the generator -----------------------------------------------------------


def test_no_list_gives_todays_fence_and_the_new_container_rules():
    """A hand install: nothing known about the network. The executor and the
    application reach what they reached; the runner, the search engine and the
    cloud executor reach public addresses only; the customer's own server and
    its other traffic are left alone."""
    text = rules()
    assert chain(text, "from_executor").strip().endswith("accept")
    fenced = chain(text, "from_containers")
    assert 'iifname { "rz-runner", "rz-search", "rz-cloud" } ip daddr @private4 drop' in fenced
    for private in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"):
        assert private in text, f"the fenced containers are not denied {private}"
    assert 'iifname != "rz-*" oifname != "rz-*" accept' in chain(text, "forward"), "other traffic on the host is judged"
    assert "ct status dnat accept" in chain(text, "from_outside"), "the published web port is closed on a hand install"
    host = chain(text, "input")
    assert "policy accept" in host and "policy drop" not in text, "a hand install's own services would be shut"
    assert 'iifname != { "rz-runner", "rz-search", "rz-cloud", "rz-backend", "rz-clouddb" } accept' in host
    assert "dport 22" not in text and "dport { 80, 443 }" not in text


def test_the_table_is_replaced_in_one_step_and_the_rule_set_is_never_flushed():
    for text in (rules(), rules(THREE_VLANS, FULL)):
        lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
        assert lines[:2] == ["table inet rezztor_guard", "delete table inet rezztor_guard"]
        assert not [line for line in lines if line.startswith("flush")], "that would delete Docker's rules too"
        assert text.count("table inet rezztor_guard {") == 1
        assert "priority -10" in text, "before Docker's own chains, which sit at 0"


def test_on_the_runners_network_only_the_runners_sshd_takes_a_connection():
    """The runner and the application share rz-runner, and Docker's network has
    no direction: agent code reached the application's port there and could try
    passwords, which the login throttle does not count for a private address
    (agent-safety review S8). Same-bridge traffic reaches the inet hooks only
    with br_netfilter, so the rule is in a bridge table of ours, replaced the
    same way as the inet one."""
    for text in (rules(), rules(THREE_VLANS, FULL)):
        bridge = text[text.index("table bridge rezztor_guard\ndelete table bridge rezztor_guard\n"):]
        assert bridge.count("table bridge rezztor_guard {") == 1
        assert "type filter hook forward priority -10; policy accept;" in bridge
        assert 'meta ibrname "rz-runner" tcp flags & (syn | ack) == syn tcp dport != 2222 drop' in bridge
        assert " accept\n" not in bridge.split("policy accept;")[1], "the bridge table only drops"
    assert guard.RUNNER_BRIDGE in guard.FENCED
    from plugins._code_execution.helpers import runner_pool

    assert guard.RUNNER_SSH_PORT == runner_pool.SSH_PORT, "the guard would close the port the application dials"


def test_three_vlans_only_the_executor_reaches_devices_and_the_host_is_closed():
    text = rules(THREE_VLANS, FULL, dns=["10.0.30.1"], ssh=True)
    assert 'set dev_if { type ifname; elements = { "vlan100", "vlan200" } }' in text
    assert 'set ui_if { type ifname; elements = { "vlan300" } }' in text
    assert text.count("elements = { 10.0.100.0/24, 10.0.200.0/24, 10.20.0.0/16, 198.51.100.0/24 }") == 2, \
        "every network here carries devices only: where the executor may go is what is closed to the others"
    assert "10.0.30.0/24" not in text.split("set private4")[0], "the user network is not a device network"
    assert "elements = { 10.0.30.1 . tcp . 53, 10.0.30.1 . udp . 53 }" in text
    assert "elements = { 10.20.0.9 . tcp . 17774 }" in text
    executor = chain(text, "from_executor")
    assert executor.strip().endswith("drop"), "the executor would reach the internet"
    assert "oifname @dev_if accept" in executor and "ip daddr @dev_net4 accept" in executor
    others = chain(text, "from_containers")
    assert others.index("@app_to_dev accept") < others.index("oifname @dev_if drop") < others.index("@private4 drop")
    assert 'iifname != "rz-*" oifname != "rz-*" drop' in chain(text, "forward"), "the appliance would route between networks"
    assert chain(text, "from_outside").strip().splitlines()[-1].strip() == "drop" and "dnat" not in text
    host = chain(text, "input")
    assert "policy drop" in host
    assert "iifname @ui_if tcp dport { 80, 443 } accept" in host
    assert "iifname @ui_if tcp dport 22 accept" in host
    assert "icmp type echo-request limit rate 10/second accept" in host


def test_ssh_answers_only_while_it_is_switched_on_and_never_on_a_hand_install():
    assert "dport 22" not in rules(THREE_VLANS, FULL, ssh=False)
    assert "dport 22" in rules(THREE_VLANS, FULL, ssh=True)
    assert "dport 22" not in rules(THREE_VLANS, {"form": "small"}, ssh=True), "the small form does not touch the host's ports"
    flat = rules(None, {"form": "full"}, ssh=True)
    assert 'iifname != "rz-*" iifname != @dev_if tcp dport 22 accept' in flat


def test_a_devices_only_list_fences_the_executor_and_keeps_the_web_interface_off_device_networks(capsys, tmp_path):
    doc = listing(net("Management", ["devices"], "eth1", "10.0.100.5/24", ["10.20.0.0/16"], "10.0.100.1"))
    small = rules(doc)
    assert 'set dev_if { type ifname; elements = { "eth1" } }' in small
    assert "elements = { 10.0.100.0/24, 10.20.0.0/16 }" in small
    assert chain(small, "from_executor").strip().endswith("drop")
    full = rules(doc, {"form": "full"})
    assert 'iifname != "rz-*" iifname != @dev_if tcp dport { 80, 443 } accept' in chain(full, "input")
    # The full form says so out loud: no listed network would answer the web interface.
    (tmp_path / "networks.json").write_text(json.dumps(doc))
    (tmp_path / "guard.json").write_text('{"form": "full"}')
    assert guard.main(["print", "--networks", str(tmp_path / "networks.json"), "--conf", str(tmp_path / "guard.json"),
                       "--resolv", os.devnull, "--ssh-flag", str(tmp_path / "none")]) == 0
    assert "no network has the role 'users'" in capsys.readouterr().err


def test_a_device_subnet_on_public_addresses_is_still_a_device_network():
    """The five private ranges would let the runner through to it."""
    text = rules(listing(
        net("Lab", ["devices"], "eth1", "198.51.100.5/24", ["203.0.113.0/24"]),
        net("Office", ["internet", "users"], "eth0", "192.168.1.5/24", gateway="192.168.1.1"),
    ))
    assert "elements = { 198.51.100.0/24, 203.0.113.0/24 }" in text
    others = chain(text, "from_containers")
    assert "ip daddr @dev_only4 drop" in others and others.index("@dev_only4 drop") < others.index("@private4 drop")


def test_a_list_with_no_internet_role_still_fences_everything():
    text = rules(listing(
        net("Devices", ["devices"], "eth1", "10.0.100.5/24"),
        net("Users", ["users"], "eth0", "10.0.30.5/24"),
    ), {"form": "full"})
    assert chain(text, "from_executor").strip().endswith("drop")
    assert "@private4 drop" in chain(text, "from_containers")
    assert "iifname @ui_if tcp dport { 80, 443 } accept" in chain(text, "input")


def test_only_a_network_for_devices_alone_is_closed_to_the_application():
    """A network that is also for the internet or for users (one flat network
    written into a list, or a combined one) is a device network for the executor
    and stays open to the application: a model server or an identity provider on
    the customer's own network must keep working."""
    text = rules(listing(net("LAN", ["internet", "users", "devices"], "eth0", "192.168.1.5/24", ["10.20.0.0/16"], "192.168.1.1")))
    assert "set dev_if { type ifname; }" in text
    assert "set dev_net4 { type ipv4_addr; flags interval; elements = { 10.20.0.0/16, 192.168.1.0/24 } }" in text
    assert "set dev_only4 { type ipv4_addr; flags interval; }" in text, "a combined network is closed to the application"
    assert "ip daddr @dev_net4 accept" in chain(text, "from_executor") and chain(text, "from_executor").strip().endswith("drop")
    others = chain(text, "from_containers")
    assert "@dev_net4" not in others, "the application is fenced out of a network that is not for devices alone"
    assert "@private4 drop" in others, "the runner and the search engine still reach no private address"
    mixed = rules(THREE_COMBINED, FULL)
    assert "set dev_only4 { type ipv4_addr; flags interval; elements = { 10.0.100.0/24, 10.0.200.0/24, 10.20.0.0/16, 198.51.100.0/24 } }" in mixed
    assert "10.0.30.0/24" in mixed.split("set dev_only4")[0], "the executor may enter the combined network"


def test_ssh_answers_only_on_the_interfaces_guard_json_names():
    """The console asks per network whether SSH may answer there. Without the
    answer it is the networks for users, as before."""
    assert "iifname @ui_if tcp dport 22 accept" in rules(THREE_VLANS, FULL, ssh=True)
    named = rules(THREE_VLANS, {**FULL, "ssh_if": ["vlan100"]}, ssh=True)
    assert 'set ssh_if { type ifname; elements = { "vlan100" } }' in named
    assert "iifname @ssh_if tcp dport 22 accept" in named and "@ui_if tcp dport 22" not in named
    assert "iifname @ui_if tcp dport { 80, 443 } accept" in named, "the web interface does not follow the SSH answer"
    nowhere = rules(THREE_VLANS, {**FULL, "ssh_if": []}, ssh=True)
    assert "set ssh_if { type ifname; }" in nowhere and "iifname @ssh_if tcp dport 22 accept" in nowhere
    assert "dport 22" not in rules(THREE_VLANS, {**FULL, "ssh_if": ["vlan300"]}, ssh=False)


@pytest.mark.parametrize("change, message", [
    ({"subnet": "172.31.250.0/24", "address": "172.31.250.9/24"}, "overlaps 172.31.248.0/21"),
    ({"routed_subnets": ["172.31.0.0/16"]}, "overlaps 172.31.248.0/21"),
    ({"routed_subnets": ["0.0.0.0/0"]}, "default route"),
    ({"routed_subnets": ["0.0.0.0/1"]}, "default route"),
    ({"routed_subnets": ["8.0.0.0/7"]}, "default route"),
])
def test_a_network_inside_the_container_block_is_refused(change, message):
    with pytest.raises(guard.GuardError, match=message):
        rules(listing({**net("VLAN 100", ["devices"], "vlan100", "10.0.100.5/24"), **change}))


def test_a_changed_container_block_moves_the_overlap_check_with_it():
    doc = listing(net("VLAN 100", ["devices"], "vlan100", "172.31.250.5/24"))
    assert "172.31.250.0/24" in rules(doc, {"container_block": "10.99.248.0/21"})
    with pytest.raises(guard.GuardError, match="overlaps 10.99.248.0/21"):
        rules(listing(net("VLAN 100", ["devices"], "vlan100", "10.99.250.5/24")), {"container_block": "10.99.248.0/21"})


def test_two_networks_with_the_same_subnet_or_the_same_interface_are_refused():
    with pytest.raises(guard.GuardError, match="overlaps 10.0.100.0/24 of 'VLAN 100'"):
        rules(listing(net("VLAN 100", ["devices"], "vlan100", "10.0.100.5/24"), net("VLAN 101", ["devices"], "vlan101", "10.0.100.129/25")))
    with pytest.raises(guard.GuardError, match="already used by 'VLAN 100'"):
        rules(listing(net("VLAN 100", ["devices"], "vlan100", "10.0.100.5/24"), net("VLAN 200", ["devices"], "vlan100", "10.0.200.5/24")))


@pytest.mark.parametrize("change", [
    {"address": "10.0.100.300/24"},
    {"address": "10.0.101.5/24"},                   # not inside its own subnet
    {"address": "fd00::5/64", "subnet": "fd00::/64"},
    {"subnet": "10.0.100.5/24"},                    # host bits set
    {"subnet": "ten.zero"},
    {"subnet": None},
    {"gateway": "10.0.100"},
    {"gateway": True},                              # not text: it would parse as 0.0.0.1
    {"address": 167797765},
    {"subnet": True},
    {"routed_subnets": ["10.20.0.9/16"]},
    {"routed_subnets": "10.20.0.0/16"},
    {"roles": []},
    {"roles": ["devices", "printers"]},
    {"roles": "devices"},
    {"interface": 'vlan100" } ; flush ruleset ; #'},
    {"interface": "an-interface-name-too-long"},
    {"interface": "rz-app"},
    {"interface": ""},
])
def test_a_bad_address_or_name_is_refused_and_never_reaches_a_rule(change):
    entry = {**net("VLAN 100", ["devices"], "vlan100", "10.0.100.5/24"), **change}
    with pytest.raises(guard.GuardError):
        rules(listing(entry))


@pytest.mark.parametrize("doc", [[], {"version": 2, "networks": []}, {"version": 1}, {"version": 1, "networks": ["vlan100"]}])
def test_a_list_of_the_wrong_shape_is_refused(doc):
    with pytest.raises(guard.GuardError):
        rules(doc)


@pytest.mark.parametrize("conf", [
    {"form": "open"},
    {"infra": [{"address": "10.0.30.8", "protocol": "icmp", "port": 3128}]},
    {"infra": [{"address": "10.0.30.8", "protocol": "tcp", "port": 0}]},
    {"infra": [{"address": "10.0.30.8", "protocol": "tcp", "port": "3128 } ; drop"}]},
    {"app_services": [{"address": "proxy.example.com", "protocol": "tcp", "port": 443}]},
    {"app_services": "10.20.0.9"},
    {"infra": [{"address": True, "protocol": "tcp", "port": 53}]},
    {"container_block": "172.31.248.1/21"},
    {"ssh_if": "vlan300"},
    {"ssh_if": ['vlan300" } ; flush ruleset ; #']},
    {"ssh_if": ["rz-app"]},
    [],
])
def test_a_bad_guard_file_is_refused(conf):
    with pytest.raises(guard.GuardError):
        rules(None, conf)


def test_the_dns_servers_come_from_the_hosts_resolver_files(tmp_path):
    """Docker hands a container the same servers, so a fenced container must be
    let through to exactly those, on port 53."""
    stub, real = tmp_path / "stub", tmp_path / "real"
    stub.write_text("# systemd-resolved\nnameserver 127.0.0.53\nnameserver ::1\noptions edns0\n")
    real.write_text("nameserver 10.0.30.1\nnameserver 2001:db8::1\nnameserver 9.9.9.9\nsearch example.com\n")
    found = guard.dns_servers(f"{tmp_path / 'missing'}:{stub}:{real}")
    assert [str(a) for a in found] == ["10.0.30.1", "9.9.9.9"]
    # Docker reads one of the two files, and which one depends on the first. Both are let through.
    other = tmp_path / "other"
    other.write_text("nameserver 10.0.30.2\nnameserver 10.0.30.1\n")
    assert [str(a) for a in guard.dns_servers(f"{real}:{other}")] == ["10.0.30.1", "9.9.9.9", "10.0.30.2"]
    assert guard.dns_servers(str(tmp_path / "missing")) == []
    text = rules(dns=["10.0.30.1"], conf={"infra": [{"address": "10.0.30.8", "protocol": "tcp", "port": 3128}]})
    assert "elements = { 10.0.30.1 . tcp . 53, 10.0.30.1 . udp . 53, 10.0.30.8 . tcp . 3128 }" in text
    # With device networks known, the executor may ask the DNS servers and nothing else outside them.
    executor = chain(rules(THREE_VLANS, FULL, dns=["10.0.30.1"]), "from_executor")
    assert "th dport 53 ip daddr . meta l4proto . th dport @infra accept" in executor


# --- the rules, loaded and tried ----------------------------------------------

NEEDED = ("unshare", "nsenter", "nft", "socat", "ip", "ping")


def _namespaces():
    """Why the namespace half cannot run here, or None when it can."""
    missing = [tool for tool in NEEDED if not shutil.which(tool)]
    if missing:
        return f"the namespace proof needs {', '.join(missing)} on this machine"
    try:
        probe = subprocess.run(["unshare", "-Urn", "nft", "list", "ruleset"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"`unshare -Urn` did not run here: {error}"
    if probe.returncode:
        return ("this machine does not let an ordinary user make a private network namespace "
                f"(`unshare -Urn nft list ruleset`: {probe.stderr.strip() or probe.returncode})")
    return None


needs_namespaces = pytest.mark.skipif(_namespaces() is not None, reason=str(_namespaces()))


def write_rules(tmp_path, doc, conf, dns, ssh):
    files = {"networks": tmp_path / "networks.json", "conf": tmp_path / "guard.json", "resolv": tmp_path / "resolv.conf",
             "ssh": tmp_path / "ssh-on", "out": tmp_path / "guard.nft"}
    if doc is not None:
        files["networks"].write_text(json.dumps(doc))
    if conf is not None:
        files["conf"].write_text(json.dumps(conf))
    files["resolv"].write_text("".join(f"nameserver {address}\n" for address in dns))
    if ssh:
        files["ssh"].touch()
    argv = [str(TOOL), "print", "--networks", str(files["networks"]), "--conf", str(files["conf"]),
            "--resolv", str(files["resolv"]), "--ssh-flag", str(files["ssh"])]
    files["out"].write_text(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)
    return files


def proof(topology, rules_file, **expect):
    """Run the proof; return its two runs, each a list of (PASS|FAIL, ok|no, text)."""
    env = {**os.environ, "TOPOLOGY": topology, "GUARD": str(rules_file or ""), "FORM": "full", "SSH": "on", **expect}
    done = subprocess.run(["unshare", "-Urn", "bash", str(PROOF)], env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and "ERROR" not in done.stdout, done.stdout + done.stderr
    runs = [re.findall(r"^(PASS|FAIL)  want=(ok|no)  (.*)$", part, re.M) for part in done.stdout.split("== ")[1:]]
    assert len(runs) == 2, done.stdout
    return runs, done.stdout


CASES = {
    # topology, network list, guard.json, DNS servers, SSH switch, what the proof is told to expect
    "three VLANs on a trunk, the appliance": ("three-vlan", THREE_VLANS, FULL, ["10.0.30.1"], True, {}),
    "three VLANs on a trunk, SSH off": ("three-vlan", THREE_VLANS, FULL, ["10.0.30.1"], False, {}),
    "one flat network, a hand install": ("flat", None, None, ["192.168.50.1"], False, {}),
    "one flat network, the appliance, SSH off": ("flat", None, {"form": "full"}, ["192.168.50.1"], False, {}),
    "one flat network, the appliance, SSH on": ("flat", None, {"form": "full"}, ["192.168.50.1"], True, {}),
    # A network for devices AND for the internet or users stays open to the application.
    "three VLANs, the user network also carries devices": ("three-vlan", THREE_COMBINED, FULL, ["10.0.30.1"], True, {"COMBINED": "1"}),
    "one flat network written into a list": ("flat", FLAT_LISTED, {"form": "full"}, ["192.168.50.1"], True, {"COMBINED": "1"}),
    # SSH is switched on, but the console said it may answer on no network.
    "three VLANs, SSH on but allowed on no network": ("three-vlan", THREE_VLANS, {**FULL, "ssh_if": []}, ["10.0.30.1"], True, {"SSH": "off"}),
}


@needs_namespaces
@pytest.mark.parametrize("case", CASES)
def test_the_generated_rules_hold_in_a_private_network_before_and_after_a_docker_restart(case, tmp_path):
    topology, doc, conf, dns, ssh, expect = CASES[case]
    files = write_rules(tmp_path, doc, conf, dns, ssh)
    form = (conf or {}).get("form", "small")
    runs, output = proof(topology, files["out"], **{"FORM": form, "SSH": "on" if ssh else "off", **expect})
    failed = [line for line in output.splitlines() if line.startswith("FAIL")]
    assert not failed, "\n".join(failed)
    # Research 04 proved 28 cases twice. Each run here holds at least those.
    assert all(len(run) >= 28 for run in runs) and sum(map(len, runs)) >= 56, [len(run) for run in runs]
    assert len(runs[0]) == len(runs[1]), "the run after the Docker restart is not the same set of checks"


@needs_namespaces
@pytest.mark.parametrize("topology", ["three-vlan", "flat"])
def test_without_the_guard_every_blocked_path_is_open_so_the_proof_can_see_a_hole(topology):
    """A check that still says "blocked" with no guard loaded is blocked by the
    test's own plumbing, and proves nothing about the rules."""
    (first, _), output = proof(topology, None, FORM="small")
    closed_anyway = [text for verdict, want, text in first if want == "no" and verdict == "PASS"]
    assert not closed_anyway, "blocked without any guard:\n" + "\n".join(closed_anyway)
    broken = [text for verdict, want, text in first if want == "ok" and verdict == "FAIL"]
    assert not broken, "the test network itself does not work:\n" + "\n".join(broken)
    assert sum(1 for _, want, _ in first if want == "no") >= (19 if topology == "three-vlan" else 10)


def _apply(tmp_path, files, extra=""):
    """`rezztor-guard apply` in a private namespace, then the table that is loaded."""
    script = (f'{TOOL} apply --networks {files["networks"]} --conf {files["conf"]} --resolv {files["resolv"]} '
              f'--ssh-flag {files["ssh"]} --out {files["out"]}; echo "rc=$?"; {extra} nft list table inet rezztor_guard')
    return subprocess.run(["unshare", "-Urn", "sh", "-c", script], capture_output=True, text=True, timeout=60)


@needs_namespaces
def test_apply_loads_the_table_beside_another_and_loading_twice_leaves_one(tmp_path):
    files = write_rules(tmp_path, THREE_VLANS, FULL, ["10.0.30.1"], True)
    files["out"].unlink()
    other = "nft add table ip docker_like; nft add chain ip docker_like c '{ type filter hook forward priority 0; }';"
    script = (f'{other} {TOOL} apply --networks {files["networks"]} --conf {files["conf"]} --resolv {files["resolv"]} '
              f'--ssh-flag {files["ssh"]} --out {files["out"]} && {TOOL} apply --networks {files["networks"]} '
              f'--conf {files["conf"]} --resolv {files["resolv"]} --ssh-flag {files["ssh"]} --out {files["out"]} && nft list ruleset')
    done = subprocess.run(["unshare", "-Urn", "sh", "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count("table inet rezztor_guard {") == 1
    assert "table ip docker_like {" in done.stdout, "loading the guard removed somebody else's table"
    assert files["out"].read_text().startswith("# Written by rezztor-guard")


@needs_namespaces
def test_a_list_it_cannot_use_never_leaves_the_machine_without_a_fence(tmp_path):
    """A broken list at boot must not mean no rules. With nothing loaded before,
    the no-list rules go in; with good rules on disk, those stay."""
    files = write_rules(tmp_path, None, {"form": "full"}, ["192.168.50.1"], False)
    files["out"].unlink()
    files["networks"].write_text('{"version": 1, "networks": [{"name": "x", "roles": ["devices"], "interface": "eth1"}]}')
    done = _apply(tmp_path, files)
    assert "rc=1" in done.stdout, "a refused list must be visible as a failed unit"
    assert "loaded the rules for one flat network instead" in done.stderr
    assert "policy drop" in done.stdout and "@private4 drop" in done.stdout, "the form of guard.json is kept"

    files["networks"].write_text(json.dumps(THREE_VLANS))
    done = _apply(tmp_path, files)
    assert "rc=0" in done.stdout and '"vlan100"' in done.stdout
    good = files["out"].read_text()
    for broken in ("{ not json", "[" * 100000):
        files["networks"].write_text(broken)
        done = _apply(tmp_path, files)
        assert "rc=1" in done.stdout and "loaded the last good rules instead" in done.stderr, done.stderr[-300:]
        assert '"vlan100"' in done.stdout and files["out"].read_text() == good
    assert sorted(path.name for path in tmp_path.iterdir()) == ["guard.json", "guard.nft", "networks.json", "resolv.conf"], \
        "a refused load left a temporary file beside the good one"


@needs_namespaces
def test_a_rule_file_on_disk_is_always_one_the_kernel_took(tmp_path):
    """`apply` loads first and keeps the file second. A rule set that fails to
    load must never become the "last good" one the next boot falls back to."""
    files = write_rules(tmp_path, THREE_VLANS, FULL, ["10.0.30.1"], True)
    good = files["out"].read_text()
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import importlib.machinery, importlib.util, sys\n"
        f"loader = importlib.machinery.SourceFileLoader('g', {str(TOOL)!r})\n"
        "g = importlib.util.module_from_spec(importlib.util.spec_from_loader('g', loader)); loader.exec_module(g)\n"
        "g.rules = lambda *a, **k: 'table inet rezztor_guard { chain c { nonsense } }'\n"
        "print('rc=%s' % g.main(sys.argv[1:]))\n"
    )
    argv = (f'apply --networks {files["networks"]} --conf {files["conf"]} --resolv {files["resolv"]} '
            f'--ssh-flag {files["ssh"]} --out {files["out"]}')
    done = subprocess.run(["unshare", "-Urn", "sh", "-c", f"python3 {driver} {argv}; nft list table inet rezztor_guard"],
                          capture_output=True, text=True, timeout=60)
    assert "rc=1" in done.stdout and "loaded the last good rules instead" in done.stderr, done.stdout + done.stderr
    assert files["out"].read_text() == good and '"vlan100"' in done.stdout


@needs_namespaces
@pytest.mark.parametrize("doc, conf", [(None, None), (None, {"form": "full"}), (THREE_VLANS, FULL), (THREE_VLANS, None)])
def test_nft_accepts_every_generated_file(doc, conf, tmp_path):
    files = write_rules(tmp_path, doc, conf, ["10.0.30.1", "9.9.9.9"], True)
    done = subprocess.run(["unshare", "-Urn", "nft", "-c", "-f", str(files["out"])], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


# --- the runner's own loopback -------------------------------------------------


def test_the_runner_rules_keep_the_slot_accounts_off_loopback_but_for_dockers_resolver():
    """One container, one account per user (ADR 0038): a server one account
    started on 127.0.0.1 handed another its 0600 files (agent-safety review S4).
    The rules go into the runner's own namespace, for exactly the image's slot
    uids, and only drop."""
    text = guard.runner_rules(2000, 128)
    lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert lines[:2] == ["table inet rezztor_runner", "delete table inet rezztor_runner"]
    assert 'oifname "lo" meta skuid 2000-2127 ct original ip daddr != 127.0.0.11 drop' in lines
    assert 'oifname "lo" meta skuid 2000-2127 ct original proto-dst != 53 drop' in lines
    assert 'oifname "lo" meta skuid 2000-2127 meta nfproto ipv6 drop' in lines
    assert "meta skuid 2000-2127 fib daddr type { broadcast, multicast } drop" in lines
    assert "meta skuid 2000-2127 ct state invalid,untracked drop" in lines
    assert not [line for line in lines if line.endswith(" accept")]
    assert "skuid 2000-2000 " in guard.runner_rules(2000, 1)
    for base, count in ((0, 128), (2000, 0), ("2000", 128), (2000, True), (2000, 70000)):
        with pytest.raises(guard.GuardError):
            guard.runner_rules(base, count)


SLOT_PASSWD = "root:x:0:0:root:/root:/bin/bash\neagent:x:1000:1000::/home/eagent:/bin/bash\n" + "".join(
    f"eagent-{i:03d}:x:{2000 + i}:{2000 + i}::/workspace/eagent-{i:03d}:/usr/local/bin/slot-shell\n" for i in range(128))


@pytest.fixture(autouse=False)
def runner_root(tmp_path, monkeypatch):
    """What the guard reads as the runner's /etc/passwd (process 4242)."""
    monkeypatch.setattr(guard, "PROC", str(tmp_path / "proc"))
    passwd = tmp_path / "proc" / "4242" / "root" / "etc" / "passwd"
    passwd.parent.mkdir(parents=True)
    passwd.write_text(SLOT_PASSWD)
    return passwd


def _fake_tools(tmp_path, inspect_rc=0, inspect_out="4242 [\"PATH=/usr/bin\",\"RUNNER_SLOT_BASE_UID=2000\",\"RUNNER_SLOTS=128\"]",
                events=""):
    """`docker` and `nsenter` stand-ins on PATH. nsenter records what it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = inspect ]; then printf \'%s\\n\' \'{inspect_out}\'; exit {inspect_rc}; fi\n'
        f'if [ "$1" = events ]; then printf \'{events}\'; exit 0; fi\n'
        "exit 2\n")
    (bin_dir / "nsenter").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/nsenter.args\ncat >> {tmp_path}/nsenter.stdin\n')
    for tool in ("docker", "nsenter"):
        (bin_dir / tool).chmod(0o755)
    return bin_dir


def test_the_runner_rules_go_into_the_running_runner_with_the_uids_its_image_states(tmp_path, monkeypatch, runner_root):
    monkeypatch.setenv("PATH", f"{_fake_tools(tmp_path)}:{os.environ['PATH']}")
    assert guard.main(["runner"]) == 0
    assert (tmp_path / "nsenter.args").read_text().split() == ["-t", "4242", "-n", "nft", "-f", "-"]
    assert (tmp_path / "nsenter.stdin").read_text() == guard.runner_rules(2000, 128)


@pytest.mark.parametrize("inspect_rc, inspect_out, rc", [
    (1, "", 0),                                                     # no runner yet: its start loads them
    (0, "0 [\"RUNNER_SLOT_BASE_UID=2000\",\"RUNNER_SLOTS=128\"]", 0),   # stopped: the same
    (0, "4242 [\"RUNNER_SLOTS=64\"]", 1),                            # the image and its accounts disagree
    (0, "4242 [\"RUNNER_SLOT_BASE_UID=0\",\"RUNNER_SLOTS=128\"]", 1),
])
def test_a_runner_that_is_not_there_or_whose_accounts_disagree_gets_nothing(tmp_path, monkeypatch, runner_root,
                                                                          inspect_rc, inspect_out, rc):
    monkeypatch.setenv("PATH", f"{_fake_tools(tmp_path, inspect_rc, inspect_out)}:{os.environ['PATH']}")
    assert guard.main(["runner"]) == rc
    assert not (tmp_path / "nsenter.args").exists()


def test_the_slot_uids_are_read_from_the_runners_own_accounts(tmp_path, monkeypatch, runner_root):
    """The accounts are what the kernel judges; the environment only says
    what the image meant (security review, 2026-10-19). Without the variables
    the accounts decide; a gap in the range or no account at all loads nothing."""
    tools = _fake_tools(tmp_path, inspect_out='4242 ["PATH=/usr/bin"]')
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    runner_root.write_text(SLOT_PASSWD.replace(":2000:2000:", ":3000:3000:").replace(":2001:2001:", ":2999:2999:")
                           .replace("eagent-002:x:2002", "eagent-002:x:3001"))
    assert guard.main(["runner"]) == 1 and not (tmp_path / "nsenter.args").exists()
    runner_root.write_text("".join(f"eagent-{i:03d}:x:{5000 + i}:{5000 + i}::/w:/bin/sh\n" for i in range(8)))
    assert guard.main(["runner"]) == 0
    assert (tmp_path / "nsenter.stdin").read_text() == guard.runner_rules(5000, 8)
    runner_root.write_text("root:x:0:0::/root:/bin/sh\neagent:x:1000:1000::/home/eagent:/bin/bash\n")
    assert guard.main(["runner"]) == 1
    runner_root.unlink()
    assert guard.main(["runner"]) == 1


def test_a_load_that_fails_ends_the_watch_with_an_error(tmp_path, monkeypatch, capsys, runner_root):
    """A watch that carried on after a failed load would show "active" over a
    runner whose accounts reach each other (security review, 2026-10-19). It
    stops with an error-level line, and the unit retries it with a backoff."""
    tools = _fake_tools(tmp_path, inspect_out='4242 ["RUNNER_SLOTS=64"]')
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    with pytest.raises(SystemExit) as stopped:
        guard.main(["runner", "--watch"])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert "RUNNER_SLOTS=64 in its environment, 128 in its /etc/passwd" in err
    assert "<3>rezztor-guard: the loopback rules are NOT loaded" in err
    unit = (TOOL.parent / "rezztor-guard-runner.service").read_text()
    for line in ("Restart=on-failure", "RestartSteps=6", "RestartMaxDelaySec=10min", "StartLimitIntervalSec=0"):
        assert line in unit.splitlines()


def test_the_watch_loads_the_rules_once_and_again_at_every_start(tmp_path, monkeypatch, runner_root):
    """Docker makes the runner a new network namespace at every start, so the
    rules loaded into the last one are gone."""
    monkeypatch.setenv("PATH", f"{_fake_tools(tmp_path, events='c1\\nc2\\n')}:{os.environ['PATH']}")

    class Stop(Exception):
        pass

    def stop(seconds):
        raise Stop

    monkeypatch.setattr(guard.time, "sleep", stop)
    with pytest.raises(Stop):
        guard.main(["runner", "--watch"])
    assert len((tmp_path / "nsenter.args").read_text().splitlines()) == 3


@needs_namespaces
def test_the_runner_rules_hold_on_a_real_loopback(tmp_path):
    """Loaded in a private namespace, where the test is uid 0: the rules are
    generated for the image's uids and run here for uid 0, then for a range
    without it, which must leave everything open."""
    text = guard.runner_rules(2000, 128)
    targets = ["TCP:127.0.0.1:8000", "TCP:10.9.9.9:8000", "TCP:127.0.0.11:53", "UDP:127.0.0.11:53",
               "TCP:127.0.0.11:8000", "TCP6:[::1]:8000"]
    results = {}
    for name, uids in (("slot", "0-0"), ("other", "1-1")):
        rules_file = tmp_path / f"{name}.nft"
        rules_file.write_text(text.replace("2000-2127", uids))
        script = (
            "ip link set lo up; ip link add d0 type dummy; ip addr add 10.9.9.9/24 dev d0; ip link set d0 up; "
            f"nft -f {rules_file} || exit 3; "
            "for l in TCP-LISTEN:8000,bind=127.0.0.1 TCP-LISTEN:8000,bind=10.9.9.9 TCP-LISTEN:53,bind=127.0.0.11 "
            "UDP-RECVFROM:53,bind=127.0.0.11 TCP-LISTEN:8000,bind=127.0.0.11 TCP6-LISTEN:8000,bind=[::1]; do "
            "socat $l,reuseaddr,fork SYSTEM:'echo answered' 2>/dev/null & done; sleep 0.5; "
            f"for t in {' '.join(targets)}; do "
            "out=$(echo x | timeout 3 socat -T1 - $t,connect-timeout=1 2>/dev/null); "
            'case "$out" in *answered*) echo "$t ok";; *) echo "$t no";; esac; done; kill $(jobs -p) 2>/dev/null; true'
        )
        done = subprocess.run(["unshare", "-Urn", "sh", "-c", script], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stdout + done.stderr
        results[name] = dict(line.split() for line in done.stdout.splitlines() if line.split()[0] in targets)
    assert results["slot"] == {"TCP:127.0.0.1:8000": "no", "TCP:10.9.9.9:8000": "no", "TCP:127.0.0.11:53": "ok",
                               "UDP:127.0.0.11:53": "ok", "TCP:127.0.0.11:8000": "no", "TCP6:[::1]:8000": "no"}
    assert set(results["other"].values()) == {"ok"}, "a uid outside the slots was cut off, or the test sees no hole"


@needs_namespaces
def test_a_slot_reaches_no_other_by_broadcast_or_multicast(tmp_path):
    """A broadcast or multicast datagram comes back to every socket in the
    namespace through eth0, not lo, so the loopback rules alone let it through
    (security review of the S4 fix, 2026-10-19). Run with and without the rules
    for uid 0, the test's own uid in its private namespace."""
    script = (
        "ip link set lo up; ip link add e0 type veth peer name e1; ip addr add 10.9.9.9/24 brd + dev e0; "
        "ip link set e0 up; ip link set e1 up; ip route add 224.0.0.0/4 dev e0; ip route add default via 10.9.9.1; "
        '[ -z "$RULES" ] || nft -f "$RULES" || exit 3; '
        "socat -u UDP-RECV:9999,reuseaddr - > $OUT/b 2>/dev/null & "
        "socat -u UDP-RECV:9998,reuseaddr,ip-add-membership=239.1.1.1:e0 - > $OUT/m 2>/dev/null & sleep 0.5; "
        "for t in 10.9.9.255 255.255.255.255; do echo $t | socat -u - UDP-DATAGRAM:$t:9999,broadcast 2>/dev/null; done; "
        "echo multicast | socat -u - UDP-DATAGRAM:239.1.1.1:9998,ip-multicast-if=10.9.9.9 2>/dev/null; "
        "sleep 0.5; kill $(jobs -p) 2>/dev/null; cat $OUT/b $OUT/m"
    )
    rules_file = tmp_path / "runner.nft"
    rules_file.write_text(guard.runner_rules(2000, 128).replace("2000-2127", "0-0"))
    got = {}
    for name, rules_path in (("open", ""), ("fenced", str(rules_file))):
        out = tmp_path / name
        out.mkdir()
        done = subprocess.run(["unshare", "-Urn", "sh", "-c", script], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "RULES": rules_path, "OUT": str(out)})
        assert done.returncode == 0, done.stdout + done.stderr
        got[name] = done.stdout.split()
    assert got["open"] == ["10.9.9.255", "255.255.255.255", "multicast"], "the test sees no hole without the rules"
    assert got["fenced"] == []
