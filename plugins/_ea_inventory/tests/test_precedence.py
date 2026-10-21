import pytest

from plugins._ea_inventory.helpers.precedence import flatten, plan, to_fields


def dev(**over):
    base = {"vendor": "", "model": "", "serial": "", "software_version": "", "ha_group": "", "mgmt_ip": "",
            "platform": "other", "custom": {}, "field_sources": {}}
    return {**base, **over}


def src(**fields):
    return {f: {"source": s} for f, s in fields.items()}


def test_empty_current_is_filled_and_equal_after_normalisation_is_unchanged():
    p = plan(dev(serial="abc123 ", vendor="Cisco", model="C9"), {"serial": "ABC123", "vendor": "cisco", "model": "C9", "software_version": "17.3"}, "facts", set())
    assert p.propose == {"software_version": "17.3"}
    assert sorted(p.unchanged) == ["model", "serial", "vendor"]


def test_pin_blocks_before_anything_else_but_after_equality():
    d = dev(model="Human", field_sources=src(model="manual"))
    p = plan(d, {"model": "X", "vendor": "Cisco"}, "facts", {("model", "facts"), ("vendor", "facts")})
    assert p.pinned == ["model", "vendor"] and not p.propose and not p.conflicts
    assert plan(d, {"model": "Human"}, "facts", {("model", "facts")}).unchanged == ["model"]
    assert plan(d, {"model": "X"}, "facts", {("model", "solarwinds")}).conflicts  # a pin is per source


def test_human_value_wins_as_a_conflict_whatever_the_human_source():
    for source in ("manual", "extraction", "agent", "who-knows", ""):
        d = dev(model="Human", field_sources=src(model=source))
        p = plan(d, {"model": "C9300"}, "facts", set())
        assert p.conflicts == [{"field": "model", "current": "Human", "proposed": "C9300", "current_source": source}]
        assert not p.propose


def test_value_without_provenance_is_treated_as_human():
    assert plan(dev(model="Old"), {"model": "New"}, "solarwinds", set()).conflicts


def test_kept_suppresses_the_same_observed_value_only():
    d = dev(model="Human", field_sources=src(model="manual"))
    kept = {("model", "facts", "C9300")}
    p = plan(d, {"model": "C9300"}, "facts", set(), kept)
    assert p.ignored == ["model"] and not p.conflicts
    assert plan(d, {"model": "C9500"}, "facts", set(), kept).conflicts  # a new observation asks again
    assert plan(d, {"model": "C9300"}, "solarwinds", set(), kept).conflicts  # kept is per source


def test_rank_facts_overwrites_solarwinds_but_not_the_reverse_and_own_source_updates():
    d = dev(vendor="Cisco", model="C9", software_version="1", field_sources=src(vendor="solarwinds", model="facts", software_version="facts"))
    assert plan(d, {"vendor": "Cisco Systems"}, "facts", set()).propose == {"vendor": "Cisco Systems"}
    p = plan(d, {"model": "C10", "software_version": "2"}, "solarwinds", set())
    assert sorted(p.outranked) == ["model", "software_version"] and not p.propose and not p.conflicts
    assert plan(d, {"model": "C10"}, "facts", set()).propose == {"model": "C10"}


def test_fields_each_source_may_touch():
    obs = {"name": "x", "role": "r", "place_id": "s", "status": "active", "tags": ["t"], "asset_tag": "a",
           "vendor": "V", "mgmt_ip": "10.0.0.1", "platform": "iosxe", "ha_group": "g", "serial": "S1"}
    assert sorted(plan(dev(), obs, "solarwinds", set()).propose) == ["mgmt_ip", "platform", "serial", "vendor"]
    assert sorted(plan(dev(), obs, "facts", set()).propose) == ["ha_group", "serial", "vendor"]
    assert plan(dev(), obs, "manual", set()).propose == {}  # a human source has no automatic fields


def test_solarwinds_serial_only_fills_an_empty_one():
    d = dev(serial="OLD", field_sources=src(serial="solarwinds"))
    p = plan(d, {"serial": "NEW"}, "solarwinds", set())
    assert not p.propose and not p.conflicts and not p.outranked
    assert plan(dev(), {"serial": "NEW"}, "solarwinds", set()).propose == {"serial": "NEW"}
    assert plan(d, {"serial": "NEW"}, "facts", set()).propose == {"serial": "NEW"}  # facts may correct it


def test_custom_keys_share_the_custom_provenance_and_collapse_into_fields():
    d = dev(custom={"ha_role": "secondary", "rack": "r1"}, field_sources=src(custom="facts"))
    p = plan(d, {"custom": {"ha_role": "primary", "ha_peer": "SN2", "rack": "r9"}}, "facts", set())
    assert p.propose == {"custom.ha_role": "primary", "custom.ha_peer": "SN2"}  # rack is not facts' to set
    assert to_fields(d, p.propose) == {"custom": {"ha_role": "primary", "rack": "r1", "ha_peer": "SN2"}}
    h = dev(custom={"ha_role": "primary"}, field_sources=src(custom="manual"))
    assert plan(h, {"custom": {"ha_role": "secondary"}}, "facts", set()).conflicts[0]["field"] == "custom.ha_role"


def test_platform_other_counts_as_empty_and_empty_observations_are_ignored():
    p = plan(dev(platform="other"), {"platform": "iosxe", "vendor": "", "model": None}, "solarwinds", set())
    assert p.propose == {"platform": "iosxe"} and not p.unchanged


def test_flatten_and_no_mutation():
    obs = {"vendor": "V", "custom": {"ha_role": "primary"}}
    snap = {"vendor": "V", "custom": {"ha_role": "primary"}}
    assert flatten(obs) == {"vendor": "V", "custom.ha_role": "primary"}
    plan(dev(), obs, "facts", set())
    assert obs == snap


def test_facts_may_set_part_number_and_mac_but_no_other_asset_field():
    p = plan(dev(part_number="", base_mac=""), {
        "part_number": "WS-C3850", "base_mac": "aa:bb:cc:dd:ee:ff", "rack": "R1", "warranty_expiry": "2027-01-01",
        "device_type": "switch", "custodian": "x",
    }, "facts", set())
    assert p.propose == {"part_number": "WS-C3850", "base_mac": "aa:bb:cc:dd:ee:ff"}
    assert plan(dev(), {"part_number": "x", "base_mac": "aa:bb:cc:dd:ee:ff"}, "solarwinds", set()).propose == {}
