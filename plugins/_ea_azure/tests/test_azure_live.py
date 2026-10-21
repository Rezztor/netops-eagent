"""Live named reads (helpers/live.py, reads.yaml): the table's invariants, the argument rules, and every way a call can
come back (200, 202 with Location, 403, 429, a redirect to another host) against the stand-in (fake_azure.py). Needs
PostgreSQL for the stored inventory. No test reaches Microsoft; every answer shape is the plugin's reading of Microsoft's
reference, not a recording.

The owner's rule is tested here as a fact about the product: nothing in the table can stop, delete, restart or change
anything, and the only POSTs are the probes."""

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from plugins._ea_azure.helpers import client, config, credential, live
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tests.fake_azure import CLIENT_ID, TENANT_ID, FakeAzure, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install

NOW = dt.datetime(2026, 10, 3, 10, 0, 0, tzinfo=dt.timezone.utc)
VM1 = f"/subscriptions/{t.SUB}/resourcegroups/rg-vm/providers/microsoft.compute/virtualmachines/vm1"
NIC1 = f"/subscriptions/{t.SUB}/resourcegroups/rg-vm/providers/microsoft.network/networkinterfaces/nic1"
WATCHER_ID = f"/subscriptions/{t.SUB}/resourcegroups/rg-net/providers/microsoft.network/networkwatchers/networkwatcher_uaenorth"
ENTRIES = live.load()

# What the contract says is refused by name; the table must keep every one of them.
CONTRACT_NEVER = (
    "/runCommand", "/runCommands", "/sharedkey", "/listKeys", "/publishxml", "config/appsettings/list",
    "config/connectionstrings/list", "/retrieveBootDiagnosticsData", "/packetCaptures", "/troubleshoot",
    "/vpndeviceconfigurationscript", "/listRadiusSecrets", "/generatevpnprofile", "/deallocate", "/powerOff", "/restart",
    "/stop", "/redeploy", "/reimage", "/reset", "/failover",
)


# --- the table --------------------------------------------------------------------------------------------------------

def test_the_table_loads_and_only_probes_post():
    assert len(ENTRIES) >= 30
    for e in ENTRIES.values():
        assert (e.method, e.role) in (("GET", "reader"), ("POST", "diagnostics")), e.name
        assert e.requires and e.summary and e.keep
    probes = {n for n, e in ENTRIES.items() if e.method == "POST"}
    assert probes == {"effective_routes", "effective_security_rules", "ip_flow_verify", "next_hop", "nsg_diagnostics",
                      "connection_check", "vpn_bgp_peers", "vpn_learned_routes", "vpn_advertised_routes"}


def test_no_suffix_matches_a_refused_one_and_the_contract_list_is_still_refused():
    assert {n.lower() for n in CONTRACT_NEVER} <= {n.lower() for n in live.NEVER}
    for e in ENTRIES.values():
        assert not [n for n in live.NEVER if n.lower() in e.suffix.lower()], e.name


def test_no_read_is_for_a_stop_a_secret_or_a_shared_key():
    text = live.READS_FILE.read_text().lower()
    for word in ("sharedkey", "runcommand", "appsettings", "connectionstrings", "listkeys", "packetcapture", "deallocate",
                 "poweroff", "restart", "publishxml", "serialconsole"):
        assert not re.search(rf"suffix:.*{word}", text), word
    assert not [n for n in ENTRIES if re.search(r"delete|stop|start|restart|reset|scale|swap|key|secret", n)]


def test_no_keep_path_names_a_secret():
    def paths(spec):
        if isinstance(spec, str):
            return [spec]
        if isinstance(spec, dict):
            return [p for v in spec.values() for p in paths(v)]
        return [spec[0], *paths(spec[1])]

    for e in ENTRIES.values():
        for path in paths(e.keep):
            assert not [w for w in live.FORBIDDEN_KEEP if w in path.lower()], (e.name, path)


@pytest.mark.parametrize("change, why", [
    ("method: POST\n  role: reader", "diagnostics"),  # a POST that is not a probe
    ("method: GET\n  role: diagnostics", "diagnostics"),
    ("method: DELETE\n  role: reader", "nothing else"),
    ("method: PUT\n  role: reader", "nothing else"),
    ("method: PATCH\n  role: reader", "nothing else"),
    ("suffix: /runCommand", "refused"),
    ("suffix: /providers/x/listKeys", "refused"),
    ("suffix: /config/appsettings/list", "refused"),
    ("suffix: /connections/c/sharedkey", "refused"),
    ("suffix: /restart", "refused"),
    ("suffix: /deallocate", "refused"),
    ("suffix: /stop", "refused"),
    ("suffix: /virtualNetworkGateways/x/reset", "refused"),
    ("suffix: /a?b=c", "plain path"),
    ("suffix: /a/../b", "plain path"),
    ("api_version: latest", "api_version"),
    ("resource_type: Microsoft.Compute/VMs", "resource_type"),
])
def test_the_loader_refuses_an_entry_that_could_stop_delete_or_leak(change, why):
    base = ("- name: probe_read\n  summary: s\n  resource_type: microsoft.compute/virtualmachines\n  method: GET\n  suffix: /instanceView\n"
            "  api_version: '2025-04-01'\n  role: reader\n  requires: A/b/read\n  keep: {x: y}\n")
    assert list(live.parse(base))  # the base itself is fine
    key = change.split(":")[0]
    lines = [l for l in base.splitlines() if not l.strip().startswith(f"{key}:")]
    if key == "method":
        lines = [l for l in lines if not l.strip().startswith("role:")]
    edited = "\n".join(lines) + "\n  " + change + "\n"
    with pytest.raises(live.ReadsInvalid, match=why):
        live.parse(edited)


def test_the_loader_refuses_a_secret_on_the_allow_list_a_body_on_a_get_and_an_unknown_placeholder():
    base = ("- name: probe_read\n  summary: s\n  resource_type: microsoft.compute/virtualmachines\n  method: GET\n  suffix: /x\n"
            "  api_version: '2025-04-01'\n  role: reader\n  requires: A/b/read\n")
    for extra, why in (("  keep: {key: properties.sharedKey}\n", "not a path"), ("  keep: {x: properties.osProfile.adminPassword}\n", "not a path"),
                       ("  keep: {x: y}\n  body: {a: b}\n", "only a probe"), ("  keep: {x: y}\n  query: {a: '{nope}'}\n", "not an argument"),
                       ("  keep: {x: y}\n  args: {port: {type: port}}\n", "not used"), ("  keep: {x: y}\n  args: {z: {type: freetext}}\n", "needs a type")):
        with pytest.raises(live.ReadsInvalid, match=why):
            live.parse(base + extra)


def test_the_source_has_two_verbs_no_redirects_and_nothing_that_changes_azure():
    source = Path(live.__file__).read_text()
    code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
    assert re.findall(r"requests\.(\w+)\(", code) == ["post", "get"]
    assert code.count("allow_redirects=False") == 2
    assert not re.search(r"\b(?:delete|put|patch)\s*\(|DELETE|\.request\(", code)


# --- arguments --------------------------------------------------------------------------------------------------------

def check(name, **given):
    return live.check_args(ENTRIES[name], given)


@pytest.mark.parametrize("given, refusal", [
    ({"direction": "Outbound", "protocol": "tcp", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "443"}, None),
    ({"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5; rm -rf /", "remote_port": "443"}, "IP address"),
    ({"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "70000"}, "port"),
    ({"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "4 43"}, "port"),
    ({"direction": "Sideways", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "1"}, "one of"),
    ({"direction": "Outbound", "protocol": "ICMP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "1"}, "one of"),
    ({"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5"}, "needs `remote_port`"),
    ({"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.0.4", "remote_ip": "10.0.1.5", "remote_port": "1", "url": "https://evil"}, "no argument"),
])
def test_probe_arguments_are_typed(given, refusal):
    if refusal is None:
        out = check("ip_flow_verify", **given)
        assert out["protocol"] == "TCP" and out["local_port"] == "60000"  # canonical case, and the documented default
    else:
        with pytest.raises(live.Refused, match=refusal):
            check("ip_flow_verify", **given)


@pytest.mark.parametrize("duration, ok", [("PT30M", True), ("pt6h", True), ("P1D", True), ("PT1M", True), ("P2D", False),
                                          ("PT0M", False), ("P", False), ("PT", False), ("1 hour", False), ("PT24H1S", False),
                                          ("P1DT1H", False)])
def test_a_duration_is_iso_within_the_cap(duration, ok):
    if ok:
        assert check("vm_metrics", metric="Percentage CPU", duration=duration)["duration"] == duration.upper()
    else:
        with pytest.raises(live.Refused):
            check("vm_metrics", metric="Percentage CPU", duration=duration)


def test_metric_names_come_from_the_types_own_list_and_at_most_five():
    assert check("vm_metrics", metric="percentage cpu, Network In Total")["metric"] == "Percentage CPU,Network In Total"
    assert check("vm_metrics", metric=["Percentage CPU"])["duration"] == "PT1H"
    for bad in ("Http5xx", "", "Percentage CPU,Percentage CPU2", "Percentage CPU,Available Memory Bytes,VmAvailabilityMetric,Network In Total,"
                "Network Out Total,OS Disk Queue Depth"):
        with pytest.raises(live.Refused):
            check("vm_metrics", metric=bad)


def test_the_activity_log_period_is_capped_at_a_week():
    assert check("activity_log")["duration"] == "P1D"
    assert check("activity_log", duration="P7D")
    with pytest.raises(live.Refused):
        check("activity_log", duration="P8D")


# --- the call, from the stored inventory -------------------------------------------------------------------------------

@pytest.fixture
def azure(database, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret("fake-client-secret", "")
    acct = t.load()
    client.reset()
    clock = [0.0]
    monkeypatch.setattr(live, "_now", lambda: NOW)
    monkeypatch.setattr(live, "_clock", lambda: clock[0])
    monkeypatch.setattr(live, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(live, "_blocked_until", 0.0)
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        fake.acct = acct
        yield fake
    client.reset()


def add_samples(acct):
    """The resource types the table reads that the hand tenant lacks, in Prod, and a Network Watcher in uaenorth."""
    extra = [t.net("NetworkWatcher_uaenorth", live.WATCHER), t.net("fw1", "microsoft.network/azurefirewalls"),
             t.net("fwp1", "microsoft.network/firewallpolicies"), t.net("conn1", "microsoft.network/connections"),
             t.net("gw1", "microsoft.network/virtualnetworkgateways"), t.net("zone1", "microsoft.network/privatednszones"),
             t.net("pe1", "microsoft.network/privateendpoints"), t.net("agw1", "microsoft.network/applicationgateways"),
             t.net("lb1", "microsoft.network/loadbalancers"), t.net("nat1", "microsoft.network/natgateways"),
             t.net("pip1", "microsoft.network/publicipaddresses")]
    t.pull(acct, t.SUB, t.prod(second=True) + extra, dt.datetime.now(dt.timezone.utc))


def arm(fake, native_id, suffix, *answers):
    """Register what the stand-in says to one call; several answers are given in order."""
    path = (native_id + suffix).lower()
    method = next(e.method for e in ENTRIES.values() if e.suffix == suffix)
    fake.arm_answers[(method, path)] = list(answers) if len(answers) > 1 else answers[0]


def calls(fake):
    """What reached the stand-in apart from signing in."""
    return [r for r in fake.received if not r["path"].endswith("/oauth2/v2.0/token")]


INSTANCE_VIEW = {
    "computerName": "secret-host", "osName": "ubuntu", "osVersion": "24.04",
    "vmAgent": {"vmAgentVersion": "2.9", "statuses": [{"code": "ProvisioningState/succeeded", "level": "Info",
                                                       "displayStatus": "Ready", "message": "Guest Agent is running"}]},
    "extensions": [{"name": "ext1", "type": "Microsoft.Azure.Monitor", "typeHandlerVersion": "1.0", "statuses": [
        {"code": "ProvisioningState/succeeded", "level": "Info", "displayStatus": "Provisioning succeeded",
         "message": "script output: PASSWORD=hunter2"}]}],
    "statuses": [{"code": "PowerState/running", "level": "Info", "displayStatus": "VM running", "time": "2026-10-03T09:00:00Z"}],
}


def test_a_200_is_reduced_to_the_allow_list_and_a_secret_is_never_in_it(azure):
    arm(azure, VM1, "/instanceView", (200, INSTANCE_VIEW, {}))
    out = live.read("vm_instance_view", "vm1", {})
    text = json.dumps(out)
    assert out["status"] == 200 and out["read"] == "vm_instance_view" and out["resource"] == VM1
    assert out["result"]["status"][0]["code"] == "PowerState/running"
    assert out["result"]["extensions"][0]["status"][0]["status"] == "Provisioning succeeded"
    assert out["result"]["agent_version"] == "2.9" and out["rows"] >= 3 and not out["truncated"]
    assert "hunter2" not in text and "secret-host" not in text and "Guest Agent is running" not in text  # not on the allow-list
    (one,) = calls(azure)
    assert (one["method"], one["path"], one["query"]) == ("GET", VM1 + "/instanceView", "api-version=2025-04-01")
    assert one["auth"].startswith("Bearer fake-token-") and one["raw"] == b""


def test_a_value_that_looks_like_a_secret_is_masked_even_where_a_field_is_allowed(azure):
    view = {"statuses": [{"code": "Server=tcp:db;Password=hunter2;", "displayStatus": "ok", "level": "Info"}]}
    arm(azure, VM1, "/instanceView", (200, view, {}))
    assert "hunter2" not in json.dumps(live.read("vm_instance_view", "vm1", {}))


def test_a_list_is_cut_at_200_and_says_so(azure):
    events = [{"eventTimestamp": f"2026-10-03T09:{i // 60:02d}:{i % 60:02d}Z", "caller": "a@b.c", "level": "Informational"} for i in range(250)]
    azure.arm_answers[("GET", f"/subscriptions/{t.SUB}/providers/microsoft.insights/eventtypes/management/values")] = (
        200, {"value": events, "nextLink": "https://management.azure.com/next"}, {})
    out = live.read("activity_log", "vm1", {})
    assert len(out["result"]["events"]) == 200 and out["truncated"] is True


def test_the_activity_log_is_asked_by_the_stored_id_and_a_time_range_and_keeps_who_but_not_claims(azure):
    azure.arm_answers[("GET", f"/subscriptions/{t.SUB}/providers/microsoft.insights/eventtypes/management/values")] = (200, {"value": [
        {"eventTimestamp": "2026-10-03T09:30:00Z", "caller": "asha@example.com", "operationName": {"value": "Microsoft.Compute/virtualMachines/write"},
         "status": {"value": "Succeeded"}, "claims": {"appid": "x"}, "httpRequest": {"clientIpAddress": "1.2.3.4"}}]}, {})
    out = live.read("activity_log", "vm1", {"duration": "PT6H"})
    assert out["result"]["events"] == [{"time": "2026-10-03T09:30:00Z", "who": "asha@example.com",
                                        "operation": "Microsoft.Compute/virtualMachines/write", "status": "Succeeded"}]
    assert "claims" not in json.dumps(out) and "1.2.3.4" not in json.dumps(out)
    query = parse_qs(calls(azure)[0]["query"])
    assert query["api-version"] == ["2015-04-01"]
    assert query["$filter"] == [f"eventTimestamp ge '2026-10-03T04:00:00Z' and eventTimestamp le '2026-10-03T10:00:00Z' and resourceUri eq '{VM1}'"]


def test_a_name_two_resources_share_is_answered_with_both_and_not_with_an_error(azure, monkeypatch):
    """`store.Ambiguous` carries its candidates in `.candidates`; its first argument is a sentence. Reading the
    sentence as the list raised `TypeError: string indices must be integers` where the refusal should be."""
    twins = [{"native_id": f"/subscriptions/0000/resourcegroups/{g}/providers/microsoft.compute/virtualmachines/vm1",
              "name": "vm1", "type": "microsoft.compute/virtualmachines", "resource_group": g} for g in ("rg-a", "rg-b")]

    def get(ref):
        raise live.store.Ambiguous(twins)

    monkeypatch.setattr(live.store, "get", get)
    with pytest.raises(live.Refused, match=r"more than one stored resource: vm1 \(microsoft.compute/virtualmachines, rg-a\); vm1 .*rg-b"):
        live.resolve(ENTRIES["vm_metrics"], "vm1")


def test_metrics_set_the_period_when_it_runs_not_when_it_is_asked(azure):
    entry = ENTRIES["vm_metrics"]
    target = live.resolve(entry, "vm1")
    args = live.check_args(entry, {"metric": "Percentage CPU", "duration": "PT2H"})
    shown = live.describe(entry, target, args)
    assert "<now minus PT2H>" in shown[0] and "interval=PT5M" in shown[0] and shown[0].startswith("GET http://127.0.0.1")
    query = parse_qs(urlsplit(live.build(entry, target, args).url).query)
    assert query["timespan"] == ["2026-10-03T08:00:00Z/2026-10-03T10:00:00Z"] and query["metricnames"] == ["Percentage CPU"]
    assert query["aggregation"] == ["Average,Maximum"]


def test_a_resource_that_is_not_stored_removed_or_the_wrong_type_is_refused_before_any_call(azure):
    for ref, why in (("ghost", "not in the stored Azure inventory"), ("oldstor", "not in the stored"), ("app1", "is a microsoft.web/sites"),
                     ("/subscriptions/x/resourcegroups/y/providers/microsoft.compute/virtualmachines/zzz", "not in the stored")):
        with pytest.raises(live.Refused, match=why):
            live.read("vm_instance_view", ref, {})
    removed = f"/subscriptions/{t.SUB}/resourcegroups/rg-app/providers/microsoft.storage/storageaccounts/oldstor"
    with pytest.raises(live.Refused, match="removed from Azure"):
        live.read("resource_health", removed, {})
    with pytest.raises(live.Refused, match="no read named"):
        live.read("delete_vm", "vm1", {})
    assert calls(azure) == []


def test_a_probe_on_a_vm_needs_the_network_watcher_of_its_region_from_the_inventory(azure):
    args = {"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.1.4", "remote_ip": "10.0.2.5", "remote_port": "443"}
    with pytest.raises(live.Refused, match="no Network Watcher in uaenorth"):
        live.read("ip_flow_verify", "vm1", args)
    add_samples(azure.acct)
    arm(azure, WATCHER_ID, "/ipFlowVerify", (200, {"access": "Deny", "ruleName": "securityRules/DenyAllInBound", "extra": "x"}, {}))
    out = live.read("ip_flow_verify", "vm1", args)
    assert out["result"] == {"access": "Deny", "rule": "securityRules/DenyAllInBound"}
    (one,) = calls(azure)
    assert (one["method"], one["path"], one["query"]) == ("POST", WATCHER_ID + "/ipFlowVerify", "api-version=2025-09-01")
    assert json.loads(one["raw"]) == {"targetResourceId": VM1, "direction": "Outbound", "protocol": "TCP", "localIPAddress": "10.0.1.4",
                                      "localPort": "60000", "remoteIPAddress": "10.0.2.5", "remotePort": "443"}


def test_a_probe_answered_with_202_is_polled_on_the_same_host_until_it_answers(azure):
    azure.arm_answers[("GET", "/_op/1")] = [(202, {}, {"Location": f"{azure.url}/_op/1", "Retry-After": "2"}), (200, {"value": [
        {"name": "default", "source": "Default", "state": "Active", "addressPrefix": ["0.0.0.0/0"], "nextHopType": "Internet"}]}, {})]
    arm(azure, NIC1, "/effectiveRouteTable", (202, {}, {"Location": f"{azure.url}/_op/1", "Retry-After": "1"}))
    out = live.read("effective_routes", "nic1", {})
    assert out["result"]["routes"] == [{"name": "default", "source": "Default", "state": "Active", "prefixes": ["0.0.0.0/0"],
                                        "next_hop_type": "Internet"}]
    assert [(r["method"], r["path"]) for r in calls(azure)] == [("POST", NIC1 + "/effectiveRouteTable"), ("GET", "/_op/1"), ("GET", "/_op/1")]
    assert all(r["auth"].startswith("Bearer ") for r in calls(azure))


def test_a_202_that_points_at_another_host_is_not_followed(azure):
    with FakeAzure(hand_tenant()) as other:
        arm(azure, NIC1, "/effectiveRouteTable", (202, {}, {"Location": f"{other.url}/_op/9"}))
        with pytest.raises(live.ReadUnavailable, match="another address"):
            live.read("effective_routes", "nic1", {})
        assert other.received == []  # the token never went there


def test_a_redirect_to_another_host_is_refused_and_never_followed(azure):
    with FakeAzure(hand_tenant()) as other:
        arm(azure, VM1, "/instanceView", (302, {}, {"Location": f"{other.url}/stolen"}))
        with pytest.raises(live.ReadUnavailable, match="redirect") as raised:
            live.read("vm_instance_view", "vm1", {})
        assert raised.value.status == 302 and other.received == []


def test_a_202_with_no_address_or_that_never_finishes_ends_with_a_sentence(azure):
    arm(azure, NIC1, "/effectiveRouteTable", (202, {}, {}))
    with pytest.raises(live.ReadUnavailable, match="no address"):
        live.read("effective_routes", "nic1", {})
    arm(azure, NIC1, "/effectiveRouteTable", (202, {}, {"Location": f"{azure.url}/_op/2", "Retry-After": "10"}))
    azure.arm_answers[("GET", "/_op/2")] = (202, {}, {"Location": f"{azure.url}/_op/2", "Retry-After": "10"})
    with pytest.raises(live.ReadUnavailable, match="not finished after 60 seconds"):
        live.read("effective_routes", "nic1", {})


def test_a_403_names_the_missing_action_and_the_role_that_carries_it(azure):
    arm(azure, NIC1, "/effectiveRouteTable", (403, {"error": {"code": "AuthorizationFailed", "message": "client 'secret-app' has no..."}}, {}))
    with pytest.raises(live.ReadUnavailable) as raised:
        live.read("effective_routes", "nic1", {})
    message = str(raised.value)
    assert raised.value.status == 403 and "Microsoft.Network/networkInterfaces/effectiveRouteTable/action" in message
    assert "Rezztor Diagnostics" in message and "secret-app" not in message  # Azure's own text is never passed on
    arm(azure, VM1, "/instanceView", (403, {}, {}))
    with pytest.raises(live.ReadUnavailable, match="Reader role normally has it"):
        live.read("vm_instance_view", "vm1", {})


@pytest.mark.parametrize("status, text", [(404, "no such resource"), (400, "did not accept the read"), (500, "problem answering"), (409, "HTTP 409")])
def test_other_refusals_are_the_plugins_own_sentences(azure, status, text):
    arm(azure, VM1, "/instanceView", (status, {"error": {"message": "internal detail"}}, {}))
    with pytest.raises(live.ReadUnavailable, match=text) as raised:
        live.read("vm_instance_view", "vm1", {})
    assert "internal detail" not in str(raised.value)


def test_a_429_is_waited_out_twice_then_refused_and_pauses_the_next_read(azure):
    slow = (429, {}, {"Retry-After": "3"})
    arm(azure, VM1, "/instanceView", slow, slow, (200, INSTANCE_VIEW, {}))
    assert live.read("vm_instance_view", "vm1", {})["status"] == 200
    assert len(calls(azure)) == 3

    azure.received.clear()
    arm(azure, VM1, "/instanceView", slow)
    with pytest.raises(live.ReadUnavailable, match="still refused after three tries") as raised:
        live.read("vm_instance_view", "vm1", {})
    assert raised.value.status == 429 and len(calls(azure)) == 3
    azure.received.clear()
    with pytest.raises(live.ReadUnavailable, match="asked this product to wait"):
        live.read("vm_instance_view", "vm1", {})
    assert calls(azure) == []  # nothing was sent while Azure had asked for quiet


def test_a_401_signs_in_again_once(azure):
    arm(azure, VM1, "/instanceView", (401, {}, {}), (200, INSTANCE_VIEW, {}))
    assert live.read("vm_instance_view", "vm1", {})["status"] == 200
    assert sum(1 for r in azure.received if r["path"].endswith("/oauth2/v2.0/token")) == 2


def test_an_answer_that_is_not_json_or_is_too_large_is_refused(azure):
    arm(azure, VM1, "/instanceView", (200, "not json{", {}))
    with pytest.raises(live.ReadUnavailable, match="cannot read"):
        live.read("vm_instance_view", "vm1", {})
    arm(azure, VM1, "/instanceView", (200, {"statuses": [{"code": "x" * 500, "displayStatus": "y" * 500} for _ in range(150)]}, {}))
    with pytest.raises(live.ReadUnavailable, match="larger than 64 KB"):
        live.read("vm_instance_view", "vm1", {})


def test_a_vpn_connection_never_returns_its_shared_key_even_if_azure_sends_it(azure):
    add_samples(azure.acct)
    conn = f"/subscriptions/{t.SUB}/resourcegroups/rg-net/providers/microsoft.network/connections/conn1"
    body = {"properties": {"connectionStatus": "Connected", "connectionType": "IPsec", "sharedKey": "PSK-SHOULD-NEVER-APPEAR",
                           "tunnelConnectionStatus": [{"tunnel": "gw1_1", "connectionStatus": "Connected", "sharedKey": "PSK2"}]}}
    azure.arm_answers[("GET", conn)] = (200, body, {})
    out = live.read("vpn_connection", "conn1", {})
    assert out["result"]["status"] == "Connected" and out["result"]["tunnels"] == [{"tunnel": "gw1_1", "status": "Connected"}]
    assert "PSK" not in json.dumps(out)
    assert not [r for r in calls(azure) if "sharedkey" in r["path"].lower()]


# --- every read, as sent -----------------------------------------------------------------------------------------------

IP = {"direction": "Outbound", "protocol": "TCP", "local_ip": "10.0.1.4", "remote_ip": "10.0.2.5", "remote_port": "443"}
SAMPLES = {
    "vm_instance_view": ("vm1", {}), "resource_health": ("vm1", {}), "service_health": ("vm1", {}), "activity_log": ("vm1", {"duration": "PT6H"}),
    "vm_metrics": ("vm1", {"metric": "Percentage CPU,Network In Total"}), "site_metrics": ("app1", {"metric": "Http5xx"}),
    "plan_metrics": ("plan1", {"metric": "CpuPercentage"}), "firewall_metrics": ("fw1", {"metric": "NetworkRuleHit"}),
    "vpn_gateway_metrics": ("gw1", {"metric": "BgpPeerStatus"}), "appgw_metrics": ("agw1", {"metric": "FailedRequests"}),
    "lb_metrics": ("lb1", {"metric": "VipAvailability"}), "nat_gateway_metrics": ("nat1", {"metric": "PacketDropCount"}),
    "public_ip_metrics": ("pip1", {"metric": "VipAvailability"}), "diagnostic_settings": ("vnet1", {}),
    "flow_logs": ("NetworkWatcher_uaenorth", {}), "firewall_get": ("fw1", {}), "firewall_policy": ("fwp1", {}),
    "firewall_rule_groups": ("fwp1", {}), "vpn_connection": ("conn1", {}), "vpn_gateway": ("gw1", {}), "vnet_peerings": ("vnet1", {}),
    "private_dns_links": ("zone1", {}), "private_dns_records": ("zone1", {}), "private_endpoint": ("pe1", {}), "site_state": ("app1", {}),
    "site_config": ("app1", {}), "site_detectors": ("app1", {}), "effective_routes": ("nic1", {}), "effective_security_rules": ("nic1", {}),
    "ip_flow_verify": ("vm1", IP), "next_hop": ("vm1", {"source_ip": "10.0.1.4", "destination_ip": "10.0.2.5"}),
    "nsg_diagnostics": ("vm1", {"direction": "Inbound", "protocol": "TCP", "source": "10.0.1.4", "destination": "10.0.2.5", "destination_port": "22"}),
    "connection_check": ("vm1", {"protocol": "Tcp", "destination_ip": "10.0.2.5", "destination_port": "443"}),
    "vpn_bgp_peers": ("gw1", {}), "vpn_learned_routes": ("gw1", {}), "vpn_advertised_routes": ("gw1", {"peer": "10.9.9.9"}),
}


def test_every_read_has_a_sample_here():
    assert set(SAMPLES) == set(ENTRIES)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_read_is_sent_exactly_as_its_card_showed_it(azure, name):
    add_samples(azure.acct)
    entry, (ref, given) = ENTRIES[name], SAMPLES[name]
    target = live.resolve(entry, ref)
    args = live.check_args(entry, given)
    call = live.build(entry, target, args)
    shown = live.describe(entry, target, args)
    assert call.method == entry.method and shown[0].split(" ", 1)[0] == entry.method
    assert call.url.startswith(azure.url + target.base + entry.suffix + "?api-version=" + entry.api_version)
    azure.arm_answers[(entry.method, urlsplit(call.url).path.lower())] = (200, {}, {})
    out = live.read(name, ref, given)
    (sent,) = calls(azure)
    assert sent["method"] == entry.method and sent["path"].lower() == urlsplit(call.url).path.lower()
    assert (json.loads(sent["raw"]) if sent["raw"] else None) == call.body
    assert out["result"] == {} and out["status"] == 200
    if entry.method == "POST":
        assert entry.role == "diagnostics" and entry.suffix.lstrip("/") in sent["path"]
    else:
        assert sent["raw"] == b""
    assert sent["method"] in ("GET", "POST")  # the product never sends anything else
