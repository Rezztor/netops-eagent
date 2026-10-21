"""net_read: a read is requested, approved on a card, then run -- never before."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from helpers import db, files, user_scope

from plugins._ea_ansible.helpers import change_store, executor_client
from plugins._ea_ansible.helpers.change_model import APPROVED, AWAITING_APPROVAL, ChangeRecord
from plugins._ea_ansible.tools import net_read

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

DEVICE = {"name": "lab-sonic", "mgmt_ip": "172.30.30.11", "platform": "sonic_community", "status": "active"}
ENTRY = SimpleNamespace(
    name="reads/interfaces", kind="read", summary="Interfaces", platforms=("sonic_community",),
    read_commands={"sonic_community": ("show interfaces status",)},
    validate_variables=lambda v: dict(v),
)
SHOW = net_read.catalogue.load()["reads/show"]  # the real entry, read before `world` replaces `load`
GOOD = {"ok": True, "checks": [{"label": "reachable", "ok": True, "detail": ""}], "devices": [DEVICE]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(net_read.plugin_config, "is_configured", lambda agent=None: True)
    monkeypatch.setattr(net_read.plugin_config, "get_config", lambda agent=None: {"read_approval": "every_time"})
    monkeypatch.setattr(net_read.catalogue, "load", lambda: {ENTRY.name: ENTRY})
    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: ENTRY)
    monkeypatch.setattr(net_read.store, "search_devices", lambda **kw: ([DEVICE] if kw.get("name") == "lab-sonic" else [], 0))
    monkeypatch.setattr(net_read.executor_client, "health", lambda agent=None: {"catalogue_commit": "c" * 40})
    state = SimpleNamespace(preflight=GOOD, cards=[], jobs=[])
    monkeypatch.setattr(net_read.preflight, "run_preflight", lambda a, d, e: state.preflight)
    monkeypatch.setattr(NetReadTool, "_show_card", lambda self, r, e, c: state.cards.append(r.id))
    monkeypatch.setattr(
        net_read.executor_client, "run",
        lambda agent, **kw: state.jobs.append(kw) or executor_client.JobResult("j1", "succeeded", 0, (), "Gi0 up"),
    )
    with user_scope.user_scope("asha"):
        yield state


NetReadTool = net_read.NetRead


def call(**args):
    tool = NetReadTool(agent=object(), name="net_read", method=None, args=args, message="", loop_data=None)
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def only_record() -> ChangeRecord:
    (record,) = change_store.list_records()
    return record


def test_request_saves_a_read_record_and_shows_a_card_without_running(world):
    message = call(capability="reads/interfaces", devices="lab-sonic")
    record = only_record()
    assert (record.kind, record.status, record.limit) == ("read", AWAITING_APPROVAL, "lab-sonic")
    assert "show interfaces status" in record.commands
    assert 890 < record.expires_at - time.time() <= 900
    assert world.cards == [record.id] and world.jobs == []
    assert "Nothing has run" in message


def test_a_device_outside_active_inventory_gets_no_record_and_an_offer(world):
    message = call(capability="reads/interfaces", devices="rogue")
    assert "not in Inventory" in message and "inventory_stage" in message
    assert change_store.list_records() == []


def test_a_failed_precheck_gives_a_reason_and_no_card(world):
    world.preflight = {"ok": False, "devices": [DEVICE], "checks": [
        {"label": "lab-sonic: credential present", "ok": False, "detail": "no credential is stored"}]}
    message = call(capability="reads/interfaces", devices="lab-sonic")
    assert "no credential is stored" in message
    assert change_store.list_records() == [] and world.cards == []


def test_a_missing_credential_shows_the_secure_form_and_ends_the_turn(world, monkeypatch):
    from plugins._ea_interaction.helpers import cards

    shown = []
    monkeypatch.setattr(cards, "show_secret_form", lambda agent, name, **kw: shown.append(name))
    world.preflight = {"ok": False, "devices": [{**DEVICE, "credential": "lab-sonic"}], "checks": [
        {"label": "lab-sonic: credential present", "ok": False, "detail": "no credential is stored"}]}
    tool = NetReadTool(agent=object(), name="net_read", method=None,
                       args={"capability": "reads/interfaces", "devices": "lab-sonic"}, message="", loop_data=None)
    response = asyncio.new_event_loop().run_until_complete(tool.execute())
    assert shown == ["lab-sonic"] and response.break_loop is True
    assert "never ask for the value in chat" in response.message.lower()
    assert change_store.list_records() == [] and world.cards == []


def test_run_refuses_until_approved_then_runs_once_in_check_mode(world):
    call(capability="reads/interfaces", devices="lab-sonic")
    record = only_record()
    refused = call(action="run", change_id=record.id)
    assert "not approved" in refused and "press Approve on the card" in refused  # typing it is not approving
    assert world.jobs == []

    assert change_store.approve(record, "asha")
    assert "Gi0 up" in call(action="run", change_id=record.id)
    assert world.jobs[0]["check_mode"] is True and world.jobs[0]["commit"] == "c" * 40
    done = change_store.load(record.id)
    assert (done.status, done.job_id) == ("closed", "j1")
    assert "not approved" in call(action="run", change_id=record.id)  # not twice
    assert len(world.jobs) == 1


def test_run_refuses_an_approval_that_expired_or_no_longer_matches(world):
    call(capability="reads/interfaces", devices="lab-sonic")
    record = only_record()
    assert change_store.approve(record, "asha")
    record.commands = [*record.commands, "show running-config"]  # changed after approval
    change_store.save(record)
    assert "changed" in call(action="run", change_id=record.id)

    record.commands = record.commands[:-1]
    change_store.save(record)
    with db.unscoped_transaction() as conn:  # time passes: a file cannot move the register's expiry (ADR 0077)
        conn.execute("UPDATE change_approval SET expires_at = %s WHERE id = %s", (time.time() - 1, record.id))
    assert "expired" in call(action="run", change_id=record.id)
    assert world.jobs == []


def test_a_failed_job_is_classified_not_dumped(world, monkeypatch):
    call(capability="reads/interfaces", devices="lab-sonic")
    record = only_record()
    assert change_store.approve(record, "asha")
    monkeypatch.setattr(
        net_read.executor_client, "run",
        lambda agent, **kw: executor_client.JobResult("j2", "failed", 2, (), "unable to open sonic cli"),
    )
    message = call(action="run", change_id=record.id)
    assert "platform_mismatch" in message
    assert change_store.load(record.id).status == "failed"


def test_an_unknown_read_lists_the_reads_that_exist_with_their_commands(world, monkeypatch):
    def unknown(name, entries=None):
        raise net_read.catalogue.UnknownEntry(f"'{name}' is not in the playbook catalogue.")
    monkeypatch.setattr(net_read.catalogue, "resolve", unknown)
    out = call(capability="reads/vlans", devices="lab-sonic")
    assert "not in the playbook catalogue" in out and f"- {ENTRY.name}: {ENTRY.summary}" in out
    assert all(c in out for cmds in ENTRY.read_commands.values() for c in cmds)
    assert change_store.list_records() == [] and world.cards == []


def test_free_form_commands_reach_the_card_and_the_job_exactly_as_approved(world, monkeypatch):
    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: SHOW)
    refused = call(capability="reads/show", devices="lab-sonic", commands=["show vlan brief | grep 10"])
    assert "does not accept" in refused and "No pipes" in refused
    assert change_store.list_records() == [] and world.cards == []

    call(capability="reads/show", devices="lab-sonic", commands=["show vlan brief", " show ip route "])
    record = only_record()
    assert record.variables == {"command_1": "show vlan brief", "command_2": "show ip route"}
    assert record.commands == ["# lab-sonic (sonic_community)", "show vlan brief", "show ip route"]
    assert world.cards == [record.id]


# --- bundles, edit on the card, and the read_approval setting (ADR 0048) ---------------------

from plugins._ea_ansible.api.read_edit import ReadEdit  # noqa: E402
from plugins._ea_ansible.helpers import bundles  # noqa: E402


def ask(ctx="chat1", **args):
    agent = SimpleNamespace(context=SimpleNamespace(id=ctx))
    tool = NetReadTool(agent=agent, name="net_read", method=None, args=args, message="", loop_data=None)
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def edit(**body):
    return asyncio.run(ReadEdit(None, None).process(body, None))


@pytest.fixture
def show(world, monkeypatch):
    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: SHOW)
    return world


def setting(monkeypatch, mode, role="power"):
    monkeypatch.setattr(net_read.plugin_config, "get_config", lambda agent=None: {"read_approval": mode})
    monkeypatch.setattr(net_read.tool_permissions, "role_for_context", lambda ctx: role)


def test_every_shipped_bundle_loads_and_passes_the_show_pattern(tmp_path):
    loaded = bundles.load(show=SHOW)
    assert {"switch_info", "routing_info"} <= set(loaded)
    assert all({"ios", "sonic_community"} <= set(b["commands"]) for b in loaded.values())
    bad = tmp_path / "bad.yaml"
    bad.write_text("x:\n  summary: s\n  commands:\n    ios: [show vlan brief | grep 10]\n")
    with pytest.raises(bundles.BundleError):  # one line past the pattern breaks the load
        bundles.load(path=str(bad), show=SHOW)


def test_a_bundle_requests_its_commands_for_the_platform_and_shows_a_card(show):
    ask(devices="lab-sonic", bundle="switch_info")
    record = only_record()
    assert record.playbook_name == "reads/show" and record.status == AWAITING_APPROVAL
    assert record.commands[0] == "# lab-sonic (sonic_community)" and "show lldp table" in record.commands
    assert show.cards == [record.id]


def test_a_bundle_refuses_mixed_platforms_unknown_names_and_own_commands(show, monkeypatch):
    other = {**DEVICE, "name": "core-1", "platform": "ios"}
    monkeypatch.setattr(net_read.store, "search_devices", lambda **kw: ([{"lab-sonic": DEVICE, "core-1": other}[kw["name"]]], 0))
    assert "split by platform" in ask(devices="lab-sonic,core-1", bundle="switch_info")
    assert "no bundle" in ask(devices="lab-sonic", bundle="nope")
    assert "not both" in ask(devices="lab-sonic", bundle="switch_info", commands=["show version"])
    assert change_store.list_records() == []


def test_edit_replaces_the_commands_on_the_same_record_with_a_new_hash_and_expiry(show):
    ask(devices="lab-sonic", capability="reads/show", commands=["show vlan brief"])
    record = only_record()
    record.expires_at = time.time() + 60
    change_store.save(record)
    out = edit(change_id=record.id, expected_hash=record.current_hash, commands=["show ip route", "show arp"])
    assert out["ok"]
    after = change_store.load(record.id)
    assert (after.id, after.status) == (record.id, AWAITING_APPROVAL)
    assert after.commands == ["# lab-sonic (sonic_community)", "show ip route", "show arp"]
    assert after.current_hash != record.current_hash and after.expires_at - time.time() > 800
    assert after.approved_hash == "" and after.approved_by == ""
    assert out["card"]["editable"] == "true" and after.current_hash in out["card"]["payloads"]
    # The old card's hash no longer approves anything.
    assert not edit(change_id=record.id, expected_hash=record.current_hash, commands=["show arp"])["ok"]


def test_edit_runs_the_same_pattern_as_a_request(show):
    ask(devices="lab-sonic", capability="reads/show", commands=["show vlan brief"])
    record = only_record()
    out = edit(change_id=record.id, expected_hash=record.current_hash, commands=["show vlan brief | grep 10"])
    assert not out["ok"] and "No pipes" in out["error"]
    assert change_store.load(record.id).commands == record.commands


def test_edit_is_refused_after_approval_for_another_user_and_for_a_fixed_read(show, monkeypatch):
    ask(devices="lab-sonic", capability="reads/show", commands=["show vlan brief"])
    record = only_record()
    with user_scope.user_scope("mallory"):
        assert not edit(change_id=record.id, expected_hash=record.current_hash, commands=["show arp"])["ok"]
    assert change_store.approve(record, "asha")
    assert "awaiting approval" in edit(change_id=record.id, expected_hash=record.current_hash, commands=["show arp"])["error"]
    assert change_store.load(record.id).commands == record.commands

    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: ENTRY)
    ask(devices="lab-sonic", capability="reads/interfaces")
    fixed = [r for r in change_store.list_records() if r.playbook_name == "reads/interfaces"][0]
    assert "free-form" in edit(change_id=fixed.id, expected_hash=fixed.current_hash, commands=["show arp"])["error"]


def test_every_time_always_shows_a_card(world, monkeypatch):
    setting(monkeypatch, "every_time")
    ask(capability="reads/interfaces", devices="lab-sonic")
    assert only_record().status == AWAITING_APPROVAL and len(world.cards) == 1


def test_log_only_runs_a_fixed_read_without_a_card_but_free_form_and_unprivileged_still_ask(show, monkeypatch):
    setting(monkeypatch, "log_only")
    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: ENTRY)
    message = ask(capability="reads/interfaces", devices="lab-sonic")
    record = only_record()
    assert (record.status, record.approved_by, record.approval_basis) == (APPROVED, "asha", "standing:log_only")
    assert record.approved_hash == record.current_hash and show.cards == [] and record.id in message
    assert "Gi0 up" in call(action="run", change_id=record.id)  # the gate accepts it

    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: SHOW)
    ask(capability="reads/show", devices="lab-sonic", commands=["show arp"])  # free-form always asks
    ask(devices="lab-sonic", bundle="switch_info")  # a bundle is fixed
    setting(monkeypatch, "log_only", role="user")  # cannot approve, so the setting does not stand in
    ask(devices="lab-sonic", bundle="routing_info")
    statuses = sorted(r.status for r in change_store.list_records())
    # fixed read (closed after its run) and the bundle ran on standing approval; the other two asked
    assert statuses == ["approved", "awaiting_approval", "awaiting_approval", "closed"] and len(show.cards) == 2


def test_a_bundle_cannot_carry_a_line_of_the_agents_own_past_the_card(show, monkeypatch):
    """`commands` with a bundle was refused, `command_5` was not: it stayed beside the bundle's lines, and the
    standing setting approved the whole read as "a bundle is fixed". Free-form always asks (ADR 0048)."""
    setting(monkeypatch, "log_only")
    monkeypatch.setattr(net_read.catalogue, "resolve", lambda name, entries=None: SHOW)
    message = ask(devices="lab-sonic", bundle="switch_info", command_5="show running-configuration")
    assert "not both" in message and change_store.list_records() == [] and show.cards == []


def test_once_per_chat_asks_once_then_covers_that_device_in_that_chat_only(world, monkeypatch):
    setting(monkeypatch, "once_per_chat")
    ask("chat1", capability="reads/interfaces", devices="lab-sonic")
    first = only_record()
    assert first.status == AWAITING_APPROVAL and first.context_id == "chat1"
    ask("chat1", capability="reads/interfaces", devices="lab-sonic")  # still nothing approved on a card
    assert len(world.cards) == 2

    assert change_store.approve(first, "asha")
    ask("chat1", capability="reads/interfaces", devices="lab-sonic")
    covered = [r for r in change_store.list_records() if r.approval_basis]
    assert [r.approval_basis for r in covered] == ["standing:once_per_chat"] and len(world.cards) == 2
    ask("chat2", capability="reads/interfaces", devices="lab-sonic")  # another chat asks again
    assert len(world.cards) == 3
