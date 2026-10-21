"""Migration 0017: the old place text is copied, reported and dropped; a rack unit needs a rack ID."""

import pathlib

import psycopg.errors as pg
import pytest

from helpers import db

RACK = "LAB-M-LAB-L1-R101-RK01"
OLD = """
CREATE TABLE inventory_site (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
ALTER TABLE inventory_device ADD COLUMN site_id UUID REFERENCES inventory_site (id),
    ADD COLUMN building TEXT NOT NULL DEFAULT '', ADD COLUMN floor TEXT NOT NULL DEFAULT '',
    ADD COLUMN room TEXT NOT NULL DEFAULT '', ADD COLUMN rack TEXT NOT NULL DEFAULT '';
ALTER TABLE inventory_device DROP CONSTRAINT inventory_device_rack_unit_needs_rack,
    ADD CONSTRAINT inventory_device_rack_unit_needs_rack
    CHECK (rack_unit IS NULL OR place_id IS NULL OR place_id ~ '^([A-Z0-9]+-){5}[A-Z0-9]+$');
"""


def put_places(conn):
    for i, kind in enumerate(("site", "location", "building", "floor", "room", "rack")):
        parts = RACK.split("-")[: i + 1]
        conn.execute("INSERT INTO inventory_place (id, parent_id, kind, code, name) VALUES (%s,%s,%s,%s,%s)",
                     ("-".join(parts), "-".join(parts[:-1]) or None, kind, parts[-1], "-".join(parts)))


def test_the_text_is_copied_the_unadopted_site_is_kept_and_the_unit_moves_with_its_proposal(database):
    sql = pathlib.Path(db.SCHEMA_DIR, "0017_drop_old_place_text.sql").read_text()
    with db.unscoped_transaction() as conn:
        conn.execute(OLD)  # the schema as 0016 left it
        put_places(conn)
        conn.execute("INSERT INTO inventory_site (name, notes) VALUES ('Lab', ''), ('DIFC-ER1', 'ER DC')")
        conn.execute("INSERT INTO inventory_device (name, site_id, building, room, rack, rack_unit) "
                     "SELECT 'old', id, '1', '1.01', 'R01', 22 FROM inventory_site WHERE name = 'Lab'")
        conn.execute("INSERT INTO inventory_device (name, place_id, rack_unit) VALUES ('placed', %s, 3)", (RACK,))
        conn.execute("INSERT INTO inventory_device (name, rack_unit) VALUES ('unit-only', 5)")
        conn.execute("INSERT INTO inventory_device (name) VALUES ('bare')")
        conn.execute(
            "INSERT INTO inventory_proposal (kind, device_id, device_name, base_version, changes, status) "
            "SELECT 'update', id, 'old', version, %s::jsonb, 'pending' FROM inventory_device WHERE name = 'old'",
            ('{"place_id": {"old": null, "new": "%s"}}' % RACK,))
        notices = []
        conn.add_notice_handler(lambda n: notices.append(n.message_primary))
        conn.execute(sql)
        got = {r[0]: r[1:] for r in conn.execute("SELECT name, custom, rack_unit, place_id FROM inventory_device").fetchall()}
        change = conn.execute("SELECT changes FROM inventory_proposal").fetchone()[0]
        kept = conn.execute("SELECT name, notes FROM inventory_unadopted_site").fetchall()
        gone = conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'inventory_device' "
                            "AND column_name IN ('site_id','building','floor','room','rack')").fetchone()[0]
        site_table = conn.execute("SELECT to_regclass('inventory_site')").fetchone()[0]
        conn.execute("DROP TABLE inventory_unadopted_site")
    assert got["old"] == ({"old_place_text": "Lab / 1 / 1.01 / R01 U22"}, None, None)
    assert got["unit-only"] == ({"old_place_text": " U5"}, None, None)
    assert got["placed"] == ({}, 3, RACK) and got["bare"] == ({}, None, None)
    assert change["rack_unit"] == {"old": None, "new": 22}  # the unit arrives with the place
    assert kept == [("DIFC-ER1", "ER DC")] and any("DIFC-ER1" in n for n in notices)
    assert gone == 0 and site_table is None


def test_a_rack_unit_needs_a_rack_id_even_with_no_place(database):
    with db.unscoped_transaction() as conn:
        put_places(conn)
        conn.execute("INSERT INTO inventory_device (name, place_id, rack_unit) VALUES ('ok', %s, 4)", (RACK,))
    for sql, params in (
        ("INSERT INTO inventory_device (name, rack_unit) VALUES ('b', 4)", ()),
        ("INSERT INTO inventory_device (name, place_id, rack_unit) VALUES ('c', 'LAB-M-LAB', 4)", ()),
    ):
        with pytest.raises(pg.CheckViolation):
            with db.transaction(db.NO_SCOPE) as conn:
                conn.execute(sql, params)
