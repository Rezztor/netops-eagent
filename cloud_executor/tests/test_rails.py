"""Guard rails: validation, the reference pattern, and the absence of any switch for deleting."""
import pytest

from cloud_executor import rails


def test_the_defaults_change_nothing_until_told_to():
    d = rails.defaults(["azurerm_resource_group"])
    assert d["subscriptions"] == [] and d["regions"] == [] and d["allow_free_code"] is False and d["allow_unknown_price"] is False
    assert d["reference_pattern"] == r"(CR|SR)-?[0-9]{4,10}" and d["document_valid_days"] == 14
    assert not [k for k in d if "delet" in k], "deleting is not configurable"


@pytest.mark.parametrize("key", ["deletes_allowed", "max_deletes", "allow_delete"])
def test_a_switch_for_deleting_is_refused_as_an_unknown_key(key):
    with pytest.raises(ValueError, match="Deleting is not configurable"):
        rails.validate({key: 1})


@pytest.mark.parametrize("body", [
    {"subscriptions": ["not-a-guid"]}, {"regions": ["Not A Region"]}, {"max_resources": 0}, {"document_valid_days": 31},
    {"document_valid_days": 0}, {"usd_month_per_change": -1}, {"allow_free_code": "yes"}, {"vm_sizes_max_vcpu": True},
    {"resource_groups": {"existing": [], "deletes": 1}}, {"reference_pattern": "("}, {"reference_pattern": ".*"},
    {"reference_pattern": "(CR)?"}, {"reference_pattern": "x" * 101}, "text",
])
def test_a_bad_rail_is_refused(body):
    with pytest.raises(ValueError):
        rails.validate(body)


def test_save_and_load_round_trip_and_an_unreadable_file_is_the_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("REZZTOR_CLOUD_STATE_DIR", str(tmp_path))
    saved = rails.save({"regions": ["uaenorth", "uaenorth"], "max_resources": 10}, ["azurerm_resource_group"])
    assert saved["regions"] == ["uaenorth"] and rails.load()["max_resources"] == 10
    (tmp_path / "rails.json").write_text("{not json")
    assert rails.load(["azurerm_resource_group"])["subscriptions"] == []


@pytest.mark.parametrize("reference, ok", [
    ("CR-1234", True), ("cr1234", True), (" SR-12345678 ", True), ("CR-123", False), ("CR 1234", False),
    ("CR-12345678901", False), ("", False), (None, False), ("CR-1234; DROP", False), ("CR-" + "1" * 40, False),
])
def test_the_reference_is_a_full_case_insensitive_match(reference, ok):
    assert rails.reference_ok(reference, rails.defaults()) is ok


def test_a_custom_pattern_is_honoured_up_to_forty_characters():
    custom = {**rails.defaults(), "reference_pattern": r"INC[0-9]{6}"}
    assert rails.reference_ok("inc123456", custom) and not rails.reference_ok("CR-1234", custom)
