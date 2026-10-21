"""The cloud tables' own rules, straight against the database as the application role."""

import datetime as dt

import psycopg.errors as pg
import pytest

from helpers import db
from plugins._ea_azure.helpers import store


def put(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def test_checks_and_cascade(database):
    acct = store.account("T1")
    with db.transaction(db.NO_SCOPE) as conn:
        sub = put(conn, "INSERT INTO cloud_container (account_id, kind, native_id, name) VALUES (%s,'subscription','s','S') "
                        "RETURNING id", (acct,))[0]
        row = ("INSERT INTO cloud_resource (account_id, subscription_id, container_id, native_id, name, type, category, "
               "state) VALUES (%s,%s,%s,%s,'n','t',%s,%s) RETURNING id")
        rid = put(conn, row, (acct, sub, sub, "a", "compute", "ok"))[0]
    for bad in (("b", "nonsense", "ok"), ("c", "compute", "asleep")):
        with pytest.raises(pg.CheckViolation), db.transaction(db.NO_SCOPE) as conn:
            conn.execute(row, (acct, sub, sub, *bad))
    with pytest.raises(pg.UniqueViolation), db.transaction(db.NO_SCOPE) as conn:
        conn.execute(row, (acct, sub, sub, "a", "compute", "ok"))
    with pytest.raises(pg.CheckViolation), db.transaction(db.NO_SCOPE) as conn:
        conn.execute("INSERT INTO cloud_account (provider, native_id) VALUES ('aws', 'x')")
    with db.transaction(db.NO_SCOPE) as conn:  # a pull can end partial
        conn.execute("INSERT INTO inventory_source_run (source, status) VALUES ('azure', 'partial')")
        conn.execute("DELETE FROM cloud_account WHERE id = %s", (acct,))
        assert put(conn, "SELECT count(*) FROM cloud_resource")[0] == 0
    assert rid


def test_backup_round_trip(database):
    """`load_data` restores tables by name, children before parents, so every foreign key must be deferrable."""
    acct = store.account("T1")
    now = dt.datetime.now(dt.timezone.utc)
    store.apply_containers(acct, [{"id": "s1", "name": "S"}], [], now)
    run = store.inv.start_run("azure", "t")
    net = {"native_id": "/subscriptions/s1/resourcegroups/rg/providers/n/v", "name": "v", "type": "n/v", "kind": "",
           "region": "", "resource_group": "rg", "sku": {}, "tags": {}, "props": {}, "state": "ok", "state_detail": "",
           "category": "networking", "relations": [{"kind": "peering", "to_native_id": "/x", "props": {}}],
           "children": [{"native_id": "/subscriptions/s1/resourcegroups/rg/providers/n/v/subnets/a", "name": "a", "type": "n/v/subnets",
                         "kind": "", "region": "", "resource_group": "rg", "sku": {}, "tags": {}, "props": {}, "state": "ok",
                         "state_detail": "", "category": "networking", "relations": [], "children": []}]}
    store.apply_subscription(acct, "s1", [net], run_id=run, now=now)
    store.apply_subscription(acct, "s1", [], run_id=run, now=now)
    dump = db.dump_data()
    assert len(dump["tables"]["cloud_change"]["rows"]) == 2
    db.load_data(dump)
    assert db.dump_data()["tables"]["cloud_resource"]["rows"] == dump["tables"]["cloud_resource"]["rows"]


def test_a_signal_has_a_known_kind_and_goes_with_its_subscription(database):
    acct = store.account("T1")
    with db.transaction(db.NO_SCOPE) as conn:
        sub = put(conn, "INSERT INTO cloud_container (account_id, kind, native_id, name) VALUES (%s,'subscription','s','S') "
                        "RETURNING id", (acct,))[0]
        ins = "INSERT INTO cloud_signal (subscription_id, kind, target_native_id) VALUES (%s,%s,'/x')"
        conn.execute(ins, (sub, "advisor"))
    with pytest.raises(pg.CheckViolation), db.transaction(db.NO_SCOPE) as conn:
        conn.execute(ins, (sub, "nonsense"))
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("DELETE FROM cloud_container WHERE id = %s", (sub,))
        assert put(conn, "SELECT count(*) FROM cloud_signal")[0] == 0
