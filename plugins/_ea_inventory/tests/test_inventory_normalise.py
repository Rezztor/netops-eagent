import pytest

from plugins._ea_inventory.helpers.normalise import InventoryError, normalise_fields


@pytest.mark.parametrize("raw", ["N/A", "na", " none ", "Unknown", "null", "-", "0000", "aaaa", ""])
def test_placeholder_serials_become_unset(raw):
    assert normalise_fields({"serial": raw}) == {"serial": ""}


def test_values_are_canonical():
    out = normalise_fields({
        "name": "  Core-SW1 ", "serial": " fdo123 ", "mgmt_ip": "2001:DB8::0001",
        "platform": "IOSXE", "status": "Active", "tags": [" Core", "core", "dc1"],
    })
    assert out == {
        "name": "core-sw1", "serial": "FDO123", "mgmt_ip": "2001:db8::1", "platform": "iosxe",
        "status": "active", "tags": ["core", "dc1"],
    }


@pytest.mark.parametrize("fields", [
    {"name": ""}, {"name": "has space"}, {"name": "x" * 129}, {"mgmt_ip": "10.0.0.300"},
    {"platform": "windows"}, {"status": "gone"}, {"tags": [f"t{i}" for i in range(21)]},
    {"custom": {"snmp_community": "x"}}, {"custom": {"Api_Token": "x"}}, {"custom": {"a": {1, 2}}},
    {"custom": {str(i): i for i in range(51)}}, {"bogus": 1}, {"place_id": "no pe"}, {"place_id": "A-B-C-D-E-F-G"},
    {"vendor": "x" * 300},
])
def test_invalid_input_is_refused(fields):
    with pytest.raises(InventoryError):
        normalise_fields(fields)


def test_unknown_fields_are_refused_not_ignored():
    with pytest.raises(InventoryError, match="Unknown field"):
        normalise_fields({"name": "a", "version": 9})


@pytest.mark.parametrize("old", ["site_id", "building", "floor", "room", "rack"])
def test_the_old_place_fields_are_refused_as_unknown(old):
    with pytest.raises(InventoryError, match="Unknown field"):
        normalise_fields({old: "x"})


def test_place_id_is_trimmed_upper_and_may_be_cleared():
    assert normalise_fields({"place_id": " difc-ga "}) == {"place_id": "DIFC-GA"}
    assert normalise_fields({"place_id": ""}) == {"place_id": None} == normalise_fields({"place_id": None})


@pytest.mark.parametrize("raw", ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABB.CCDD.EEFF", "aabbccddeeff"])
def test_mac_forms_become_one(raw):
    assert normalise_fields({"base_mac": raw}) == {"base_mac": "aa:bb:cc:dd:ee:ff"}
    assert normalise_fields({"base_mac": ""}) == {"base_mac": ""}


@pytest.mark.parametrize("fields", [
    {"base_mac": "aa:bb:cc:dd:ee"}, {"base_mac": "aa:bb-cc:dd-ee:ff"}, {"base_mac": "gg:bb:cc:dd:ee:ff"},
    {"base_mac": "aabb.ccdd.ee"}, {"base_mac": 5},
    {"warranty_expiry": "02/03/2027"}, {"eol_date": "2027-13-01"}, {"purchase_date": "2027-2-3"},
    {"support_expiry": "20270203"}, {"warranty_expiry": 20270203},
    {"rack_unit": 0}, {"rack_unit": 61}, {"u_height": 21}, {"u_height": "x"}, {"u_height": 1.5}, {"rack_unit": True},
    {"device_type": "toaster"}, {"part_number": "x" * 300}, {"part_number": 7},
])
def test_asset_record_input_is_refused(fields):
    with pytest.raises(InventoryError):
        normalise_fields(fields)


def test_asset_record_values_are_canonical():
    out = normalise_fields({
        "device_type": " Access_Point ", "department": " IT ", "rack_unit": "22", "u_height": 2,
        "warranty_expiry": "2027-02-28", "eol_date": "", "status": "Spare",
    })
    assert out == {
        "device_type": "access_point", "department": "IT", "rack_unit": 22, "u_height": 2,
        "warranty_expiry": "2027-02-28", "eol_date": None, "status": "spare",
    }
    assert normalise_fields({"rack_unit": None, "u_height": ""}) == {"rack_unit": None, "u_height": None}
    assert normalise_fields({"status": "faulty"}) == {"status": "faulty"}
