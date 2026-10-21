import datetime as dt

from plugins._ea_inventory.helpers.reconcile import reconcile

NOW = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def dev(i, name, **over):
    base = {
        "id": i, "name": name, "vendor": "", "model": "", "software_version": "", "mgmt_ip": "", "platform": "other",
        "serial": "", "status": "active", "field_sources": {}, "role": "core", "place_id": None, "tags": [],
    }
    return {**base, **over}


def obs(name="sw1", i="n1", **over):
    base = {
        "node_id": i, "name": name, "aliases": [name], "vendor": "Cisco", "model": "C9300", "software_version": "17.3",
        "mgmt_ip": "10.0.0.1", "serial": "FOC1", "platform": "iosxe", "confidence": 0.9, "last_poll": None,
    }
    return {**base, **over}


def old(device_id, source_id="n1", days=0, source="solarwinds"):
    return {"device_id": device_id, "source": source, "source_id": source_id, "observed_at": NOW - dt.timedelta(days=days)}


def rec(observed, devices=(), observations=(), **kw):
    return reconcile(observed, list(devices), list(observations), now=NOW, **kw)


def test_no_match_creates_with_platform_only_when_confident():
    plan = rec([obs(), obs("sw2", "n2", mgmt_ip="10.0.0.2", serial="", confidence=0.4)])
    a, b = plan["create"]
    assert a["fields"] == {
        "name": "sw1", "vendor": "Cisco", "model": "C9300", "software_version": "17.3",
        "mgmt_ip": "10.0.0.1", "platform": "iosxe", "serial": "FOC1"}
    assert "platform" not in b["fields"] and "serial" not in b["fields"]


def test_matches_by_serial_by_source_id_and_by_ip():
    d = dev("d1", "other-name", serial="FOC1")
    assert rec([obs()], [d])["update"][0]["device_id"] == "d1"
    d = dev("d2", "x", mgmt_ip="10.9.9.9")
    assert rec([obs(serial="")], [d], [old("d2")])["update"][0]["device_id"] == "d2"
    d = dev("d3", "x", mgmt_ip="10.0.0.1")
    assert rec([obs(serial="")], [d])["update"][0]["device_id"] == "d3"


def test_decommissioned_device_does_not_hold_its_address():
    plan = rec([obs(serial="")], [dev("d1", "x", mgmt_ip="10.0.0.1", status="decommissioned")])
    assert len(plan["create"]) == 1 and not plan["needs_review"]


def test_hostname_alone_is_never_a_match():
    plan = rec([obs()], [dev("d1", "sw1", serial="OTHER", mgmt_ip="10.5.5.5")])
    assert plan["create"] == [] and plan["update"] == []
    [r] = plan["needs_review"]
    assert r == {"node_id": "n1", "observed_name": "sw1", "device_id": "d1", "device_name": "sw1", "reason": r["reason"]}
    assert "duplicate" in r["reason"]


def test_several_matches_need_review():
    plan = rec([obs()], [dev("d1", "a", serial="FOC1"), dev("d2", "b", mgmt_ip="10.0.0.1")])
    [r] = plan["needs_review"]
    assert (r["device_name"], r["device_id"]) == ("a", "d1") and "2 devices" in r["reason"] and not plan["update"] and not plan["create"]


def test_two_nodes_with_one_address_need_review():
    plan = rec([obs("a", "n1", serial=""), obs("b", "n2", serial="")])
    assert len(plan["create"]) == 1 and plan["needs_review"][0]["device_name"] == "a"


def test_fills_empty_and_changes_solarwinds_fields_only():
    d = dev("d1", "sw1", serial="FOC1", vendor="Cisco", model="old", software_version="16.1",
            field_sources={"model": {"source": "solarwinds"}, "software_version": {"source": "manual"}})
    [u] = rec([obs()], [d])["update"]
    assert u["fields"] == {"model": "C9300", "mgmt_ip": "10.0.0.1", "platform": "iosxe"}  # version is a person's


def test_human_and_extraction_values_win_and_serial_is_only_filled():
    d = dev("d1", "sw1", serial="FOC1", vendor="Cisco", model="Human", mgmt_ip="10.0.0.1", platform="iosxe",
            software_version="17.3", field_sources={"model": {"source": "manual"}, "vendor": {"source": "extraction"}})
    plan = rec([obs(vendor="CISCO", model="C9300")], [d])
    assert plan["update"] == [] and plan["unchanged_seen"] == []
    # vendor differs only by case (equal); a differing human model is a conflict, not a silent skip
    assert plan["conflicts"] == [{"node_id": "n1", "device_id": "d1", "device_name": "sw1", "conflicts": [
        {"field": "model", "current": "Human", "proposed": "C9300", "current_source": "manual"}]}]
    # a solarwinds-sourced serial is never rewritten either
    d2 = dev("d2", "sw2", serial="OLD", mgmt_ip="10.0.0.1", field_sources={"serial": {"source": "solarwinds"}})
    assert "serial" not in rec([obs(serial="NEW")], [d2])["update"][0]["fields"]
    # an empty serial is filled
    d3 = dev("d3", "sw3", mgmt_ip="10.0.0.1", vendor="Cisco", model="C9300", software_version="17.3", platform="iosxe")
    assert rec([obs()], [d3])["update"][0]["fields"] == {"serial": "FOC1"}


def test_never_touches_name_role_site_status_tags():
    d = dev("d1", "other-name", serial="FOC1", status="offline", role="edge", tags=["x"])
    [u] = rec([obs(name="sw1")], [d])["update"]
    assert not {"name", "role", "place_id", "status", "tags", "ha_group", "asset_tag", "custom"} & set(u["fields"])


def test_weak_platform_is_not_proposed():
    d = dev("d1", "sw1", serial="FOC1", vendor="Cisco", model="C9300", software_version="17.3", mgmt_ip="10.0.0.1")
    assert rec([obs(confidence=0.4)], [d])["update"] == []


def test_stale_devices_are_reported_not_removed():
    ds = [dev("d1", "a", serial="FOC1"), dev("d2", "b"), dev("d3", "c"), dev("d4", "d")]
    seen = [old("d1", "n1", 30), old("d2", "n2", 30), old("d3", "n3", 2), old("d4", "n4", 30, source="manual")]
    plan = rec([obs()], ds, seen)
    assert plan["stale"] == ["d2"] and plan["create"] == [] and "remove" not in plan
    assert rec([obs()], ds, seen, complete=False)["stale"] == []  # a truncated fetch proves nothing
    assert rec([obs()], ds, seen, stale_days=60)["stale"] == []
