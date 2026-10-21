import asyncio
from types import SimpleNamespace

import pytest

from plugins._ea_inventory.api.inventory_batches import InventoryBatches
from plugins._ea_inventory.api.inventory_decide_batch import InventoryDecideBatch
from plugins._ea_inventory.api.inventory_proposals import InventoryProposals
from plugins._ea_inventory.helpers import proposals, staging, store

TEXT = "sw-a FOC1\nsw-b FOC2\nsw-c FOC3\nsw-d FOC1\n"
DOC = SimpleNamespace(text=TEXT, source_id="s1")


def call(handler_class, payload):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")
    from plugins._ea_inventory.helpers import executor_sync

    calls = []
    monkeypatch.setattr(executor_sync, "sync_after_commit", lambda: calls.append(1) or {"ok": True})
    return calls


def batch(names=("sw-a", "sw-b", "sw-c"), confidences=(0.9, 0.5, None), file="f.csv"):
    records = [
        {
            "fields": {"name": n}, "evidence": {"quote": f"{n} FOC{i + 1}", "location": f"line {i + 1}"},
            **({"confidence": c} if c is not None else {}),
        }
        for i, (n, c) in enumerate(zip(names, confidences))
    ]
    return staging.stage(DOC, records, file, "bob")["batch_id"]


def test_filters_by_source_and_batch(make, events):
    make("manual-1")
    b = batch()
    assert call(InventoryProposals, {})["total"] == 4
    assert call(InventoryProposals, {"source": "extraction"})["total"] == 3
    assert call(InventoryProposals, {"source": "manual"})["total"] == 1
    assert call(InventoryProposals, {"batch_id": b, "status": "pending"})["total"] == 3
    assert call(InventoryProposals, {"batch_id": "nope"})["total"] == 0


def test_batches_are_grouped_with_counts(make, events):
    make("manual-1")
    b1, b2 = batch(file="one.csv"), batch(("sw-d",), (1,), "two.csv")
    first = proposals.list_proposals(batch_id=b1)[0][0]
    proposals.decide(first["id"], "reject", actor="alice")
    out = call(InventoryBatches, {})
    assert out["total"] == 2 and [x["batch_id"] for x in out["batches"]] == [b2, b1]
    one = out["batches"][1]
    assert one["file"] == "one.csv" and one["source"] == "extraction" and one["requested_by"] == "bob"
    assert one["counts"] == {"pending": 2, "approved": 0, "rejected": 1, "superseded": 0} and one["total"] == 3
    assert call(InventoryBatches, {"status": "rejected"})["total"] == 1
    assert call(InventoryBatches, {"limit": 1, "offset": 1})["batches"][0]["batch_id"] == b1


def test_approve_batch_applies_each_and_syncs_once(make, events, who):
    b = batch()
    out = call(InventoryDecideBatch, {"batch_id": b, "decision": "approve"})
    assert out == {"ok": True, "decided": 3, "failed": [], "skipped_low_confidence": 0, "sync": {"ok": True}}
    assert store.search_devices()[1] == 3 and len(who) == 1
    assert call(InventoryDecideBatch, {"batch_id": b, "decision": "approve"})["ok"] is False


def test_min_confidence_holds_back_low_and_unknown(make, events, who):
    b = batch()
    out = call(InventoryDecideBatch, {"batch_id": b, "decision": "approve", "min_confidence": 0.8})
    assert (out["decided"], out["skipped_low_confidence"]) == (1, 2)
    assert call(InventoryProposals, {"batch_id": b, "status": "pending"})["total"] == 2


def test_reject_batch_does_not_sync(make, events, who):
    b = batch()
    out = call(InventoryDecideBatch, {"batch_id": b, "decision": "reject", "min_confidence": 0.99, "note": "no"})
    assert (out["decided"], out["sync"], len(who)) == (3, None, 0)
    assert store.search_devices()[1] == 0


def test_stale_update_is_superseded_and_batch_goes_on(make, events):
    dev = make("sw-a", vendor="x")
    doc = SimpleNamespace(text="sw-a juniper\nsw-b FOC2", source_id="s")
    recs = [
        {"fields": {"name": "sw-a", "vendor": "juniper"}, "evidence": {"quote": "sw-a juniper", "location": "1"}},
        {"fields": {"name": "sw-b"}, "evidence": {"quote": "sw-b FOC2", "location": "2"}},
    ]
    b = staging.stage(doc, recs, "f", "bob")["batch_id"]
    p = proposals.propose("update", dev["id"], {"model": "m"}, actor="alice")
    proposals.decide(p["id"], "approve", actor="alice")
    out = call(InventoryDecideBatch, {"batch_id": b, "decision": "approve"})
    assert out["decided"] == 1 and len(out["failed"]) == 1 and "changed since" in out["failed"][0]["error"]
    statuses = {x["device_name"]: x["status"] for x in proposals.list_proposals(batch_id=b)[0]}
    assert statuses == {"sw-a": "superseded", "sw-b": "approved"}


def test_a_failure_does_not_stop_the_rest(make, events, monkeypatch):
    b = batch()
    real = proposals.decide
    seen = []

    def flaky(pid, *a, **k):
        seen.append(pid)
        if len(seen) == 1:
            raise proposals.InventoryError("boom")
        return real(pid, *a, **k)

    monkeypatch.setattr(proposals, "decide", flaky)
    out = call(InventoryDecideBatch, {"batch_id": b, "decision": "approve"})
    assert out["decided"] == 2 and out["failed"] == [{"id": seen[0], "error": "boom"}]


@pytest.mark.parametrize("payload", [
    {"batch_id": "x", "decision": "approve"}, {"batch_id": "00000000-0000-0000-0000-000000000000", "decision": "approve"},
    {"batch_id": "00000000-0000-0000-0000-000000000000", "decision": "maybe"},
])
def test_bad_requests_are_refused(database, events, payload):
    assert call(InventoryDecideBatch, payload)["ok"] is False


def test_min_confidence_is_validated(make, events):
    b = batch()
    assert call(InventoryDecideBatch, {"batch_id": b, "decision": "approve", "min_confidence": 2})["ok"] is False


def test_an_imported_file_is_shown_by_its_name_under_the_users_folder_never_the_server_path():
    from plugins._ea_inventory.helpers.proposals import shown_file

    assert shown_file("/eagent/usr/users/7f3a/uploads/estate.xlsx") == "uploads/estate.xlsx"
    assert shown_file("/somewhere/else/estate.csv") == "estate.csv"
    assert shown_file("workdir/estate-import-demo.xlsx") == "workdir/estate-import-demo.xlsx"
    assert shown_file("") == ""
