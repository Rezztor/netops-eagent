"""The place table's own rules, straight against the database as the application role."""

import pathlib

import psycopg.errors as pg
import pytest

from helpers import db

CHAIN = ["DIFC", "DIFC-GA", "DIFC-GA-GV4", "DIFC-GA-GV4-L1", "DIFC-GA-GV4-L1-R02", "DIFC-GA-GV4-L1-R02-RK01"]
KINDS = ["site", "location", "building", "floor", "room", "rack"]


def put(conn, id, kind=None, name=None, parent="auto", code=None, **extra):
    parts = id.split("-")
    parent = "-".join(parts[:-1]) or None if parent == "auto" else parent
    cols = {"id": id, "parent_id": parent, "kind": kind or KINDS[len(parts) - 1], "code": code or parts[-1],
            "name": name or id, **extra}
    conn.execute(
        f"INSERT INTO inventory_place ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(cols.values())
    )


@pytest.fixture
def tree(database):
    with db.transaction(db.NO_SCOPE) as conn:
        for id in CHAIN:
            put(conn, id)


def refused(error, sql, params=()):
    with pytest.raises(error):
        with db.transaction(db.NO_SCOPE) as conn:
            conn.execute(sql, params)


def test_six_kinds_save_in_a_chain(tree):
    with db.transaction(db.NO_SCOPE) as conn:
        got = conn.execute("SELECT kind FROM inventory_place ORDER BY id").fetchall()
    assert [k for (k,) in got] == KINDS


@pytest.mark.parametrize("id,kind,parent", [
    ("DIFC-RK1", "rack", "DIFC"),                           # a rack under a site
    ("DIFC-B1", "building", "DIFC"),                        # a building under a site
    ("DIFC-GA-F1", "floor", "DIFC-GA"),                     # a floor under a location
    ("DIFC-GA-GV4-L1-R02-RK01-X", "rack", "DIFC-GA-GV4-L1-R02-RK01"),  # a seventh level
    ("DIFC-NOPE", "location", "DIFC-GA"),                   # ID is not parent plus code
])
def test_a_misplaced_place_is_refused(tree, id, kind, parent):
    with pytest.raises((pg.CheckViolation, pg.ForeignKeyViolation)):
        with db.transaction(db.NO_SCOPE) as conn:
            put(conn, id, kind, parent=parent, code=id.split("-")[-1])


def test_duplicate_code_and_name_are_refused(tree):
    with pytest.raises(pg.UniqueViolation):
        with db.transaction(db.NO_SCOPE) as conn:
            put(conn, "DIFC-GA", name="Other")
    with pytest.raises(pg.UniqueViolation):
        with db.transaction(db.NO_SCOPE) as conn:
            put(conn, "DIFC-GB", name="difc-ga")


def test_ids_are_immutable_but_names_and_active_update(tree):
    for column, value in (("id", "ZZ"), ("parent_id", "DIFC"), ("kind", "rack"), ("code", "ZZ")):
        refused(pg.InsufficientPrivilege, f"UPDATE inventory_place SET {column} = %s WHERE id = 'DIFC-GA'", (value,))
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE inventory_place SET name = 'Renamed', active = false WHERE id = 'DIFC-GA'")
        assert conn.execute("SELECT name, active FROM inventory_place WHERE id = 'DIFC-GA'").fetchone() == ("Renamed", False)


def test_delete_with_a_child_or_a_device_is_refused(tree):
    refused(pg.ForeignKeyViolation, "DELETE FROM inventory_place WHERE id = 'DIFC-GA'")
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO inventory_device (name, place_id) VALUES ('sw1', %s)", (CHAIN[-1],))
    refused(pg.ForeignKeyViolation, "DELETE FROM inventory_place WHERE id = %s", (CHAIN[-1],))


def test_a_device_place_must_exist_and_a_rack_unit_needs_a_rack(tree):
    refused(pg.ForeignKeyViolation, "INSERT INTO inventory_device (name, place_id) VALUES ('sw1', 'NOPE')")
    refused(pg.CheckViolation, "INSERT INTO inventory_device (name, place_id, rack_unit) VALUES ('sw1', %s, 4)", (CHAIN[4],))
    refused(pg.CheckViolation, "INSERT INTO inventory_device (name, rack_unit) VALUES ('b', 4)")  # 0017: no place, no unit
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO inventory_device (name, place_id, rack_unit) VALUES ('a', %s, 4)", (CHAIN[5],))


def test_the_migration_rewrites_pending_proposals(database):
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute(
            "INSERT INTO inventory_proposal (kind, device_name, changes, status) VALUES "
            "('update', 'a', '{\"room\": {\"old\": \"\", \"new\": \"R1\"}, \"site_id\": {\"old\": null, \"new\": \"S\"}}', 'pending'), "
            "('create', 'b', '{\"name\": {\"old\": null, \"new\": \"b\"}, \"rack\": {\"old\": \"\", \"new\": \"K\"}}', 'pending'), "
            "('update', 'c', '{\"room\": {\"old\": \"\", \"new\": \"R9\"}}', 'approved')"
        )
    sql = pathlib.Path(db.SCHEMA_DIR, "0015_inventory_places.sql").read_text()
    with db.unscoped_transaction() as conn:  # the data statements of the migration, as the owner
        conn.execute(sql[sql.index("UPDATE inventory_proposal p SET"):])
        got = {r[0]: r[1:] for r in conn.execute(
            "SELECT device_name, status, changes, evidence, decided_by FROM inventory_proposal").fetchall()}
    assert got["a"] == ("superseded", {}, {"old_place_text": {"room": "R1", "site_id": "S"}}, "system:migration")
    assert got["b"] == ("pending", {"name": {"old": None, "new": "b"}}, {"old_place_text": {"rack": "K"}}, "")
    assert got["c"][0] == "approved" and got["c"][1] == {"room": {"old": "", "new": "R9"}}


def test_a_backup_restores_children_and_devices_before_their_parents(tree):
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO inventory_device (name, place_id) VALUES ('sw1', %s)", (CHAIN[-1],))
    dump = db.dump_data()
    rows = dump["tables"]["inventory_place"]["rows"]
    dump["tables"]["inventory_place"]["rows"] = rows[::-1]  # children first
    assert db.load_data(dump)["inventory_place"] == 6
    with db.transaction(db.NO_SCOPE) as conn:
        assert conn.execute("SELECT count(*) FROM inventory_place").fetchone() == (6,)
        assert conn.execute("SELECT place_id FROM inventory_device").fetchone() == (CHAIN[-1],)
