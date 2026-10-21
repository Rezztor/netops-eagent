"""inventory_places, inventory_place_save: who may do what, and what a refusal carries."""

import asyncio

import pytest

from helpers import api_policy, login, rbac
from plugins._ea_inventory.api.inventory_place_merge import InventoryPlaceMerge
from plugins._ea_inventory.api.inventory_place_save import InventoryPlaceSave
from plugins._ea_inventory.api.inventory_places import InventoryPlaces
from plugins._ea_inventory.helpers import proposals

VIEW, MANAGE, APPROVE, PLACES = (
    rbac.PERM_INVENTORY_VIEW, rbac.PERM_INVENTORY_MANAGE, rbac.PERM_INVENTORY_APPROVE, rbac.PERM_INVENTORY_PLACES,
)


def call(handler_class, payload):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload, None))


def save(**payload):
    return call(InventoryPlaceSave, payload)


@pytest.fixture
def held(monkeypatch, events):
    """The permissions the caller holds; starts as a power user."""
    perms = {VIEW, MANAGE, APPROVE}
    monkeypatch.setattr(login, "has_permission", lambda p: p in perms)
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")
    return perms


def test_the_roles_and_the_gates():
    assert PLACES in rbac.get_permissions(rbac.ROLE_ADMIN)
    assert PLACES not in rbac.get_permissions(rbac.ROLE_POWER)
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_places") == VIEW
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_place_save") == MANAGE
    assert InventoryPlaceMerge.required_permission() == PLACES


def test_a_power_user_adds_floors_rooms_and_racks_and_nothing_above(database, held):
    for refused in (
        {"action": "create", "parent_id": None, "name": "DIFC", "code": "DIFC"},
        {"action": "create", "parent_id": None, "name": "DIFC", "code": "DIFC", "chain": []},
    ):
        assert save(**refused)["ok"] is False
    held.add(PLACES)
    made = save(action="create", parent_id=None, name="DIFC", code="DIFC",
                chain=[{"name": "Gate Avenue", "code": "GA"}, {"name": "Gate Village 4", "code": "GV4"}])
    assert made["ok"] and made["place"]["id"] == "DIFC" and [p["id"] for p in made["created"]] == [
        "DIFC", "DIFC-GA", "DIFC-GA-GV4"]
    held.discard(PLACES)

    assert save(action="create", parent_id="DIFC", name="Gate Avenue 2", code="GA2")["ok"] is False  # a location
    assert save(action="create", parent_id="DIFC-GA", name="Other", code="OT")["ok"] is False  # a building
    assert save(action="create", parent_id="DIFC-GA-GV4", name="Level 1", code="L1", chain=[
        {"name": "Room 2", "code": "R02"}])["ok"] is False  # any chain
    assert save(action="update", id="DIFC-GA-GV4", name="Renamed")["ok"] is False
    assert save(action="delete", id="DIFC-GA-GV4")["ok"] is False

    floor = save(action="create", parent_id="DIFC-GA-GV4", name="Level 1", code="L1")
    room = save(action="create", parent_id="DIFC-GA-GV4-L1", name="Room 2", code="R02", room_type="server_room")
    rack = save(action="create", parent_id="DIFC-GA-GV4-L1-R02", name="Rack 1", code="RK01")
    assert (floor["ok"], room["place"]["room_type"], rack["place"]["id"]) == (True, "server_room", "DIFC-GA-GV4-L1-R02-RK01")
    assert save(action="update", id="DIFC-GA-GV4-L1-R02", active=False)["place"]["active"] is False
    assert save(action="delete", id="DIFC-GA-GV4-L1-R02-RK01")["ok"] is False  # still a power user

    held.add(PLACES)
    assert save(action="delete", id="DIFC-GA-GV4-L1-R02-RK01") == {"ok": True}
    assert save(action="update", id="DIFC-GA-GV4", name="Renamed")["place"]["name"] == "Renamed"


def test_suggest_and_a_taken_code_returns_the_existing_place(database, held):
    held.add(PLACES)
    save(action="create", parent_id=None, name="DIFC", code="DIFC")
    assert save(action="suggest", parent_id=None, name="Gate Village")["code"] == "GV"
    assert save(action="create", parent_id="DIFC", name="Gate Avenue", code="GA")["ok"]
    assert save(action="suggest", parent_id="DIFC", name="Gate Avenue")["code"] == "GA2"

    by_code = save(action="create", parent_id="DIFC", name="Something else", code="ga")
    by_name = save(action="create", parent_id="DIFC", name="gate avenue", code="XX")
    for refused in (by_code, by_name):
        assert refused["ok"] is False and refused["existing"]["id"] == "DIFC-GA" and "DIFC-GA" in refused["error"]
    assert save(action="suggest", parent_id="NOPE", name="x")["ok"] is False
    assert save(action="nonsense")["ok"] is False
    assert save(action="create", parent_id="DIFC", name="x", code="X", chain="no")["ok"] is False


def test_the_list_carries_the_room_types(database, held):
    held.add(PLACES)
    save(action="create", parent_id=None, name="DIFC", code="DIFC")
    listed = call(InventoryPlaces, {})
    assert listed["ok"] and [p["id"] for p in listed["places"]] == ["DIFC"]
    assert "server_room" in listed["room_types"]
    save(action="update", id="DIFC", active=False)
    assert call(InventoryPlaces, {"include_inactive": False})["places"] == []
