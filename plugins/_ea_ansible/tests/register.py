"""For tests: put the approval register in a state no code path writes.

`change_store.save` never writes an approval (ADR 0077), so a test that needs a record approved with no number, or
approved and expired, or left `applying` by a restart, cannot make one by saving. This writes the row as the owner
role, then the file, so a gate's refusals can be tested one condition at a time.
"""

from __future__ import annotations

from helpers import db, user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLYING, ChangeRecord


def force(record: ChangeRecord, user_id: str | None = None) -> ChangeRecord:
    """Make the register say what `record` says, and save the record."""
    fields = change_store._ROW_FIELDS
    taken = sum(1 for s in record.steps if s.get("status", "planned") != "planned")  # the steps of a set that were started
    step = max(taken, 1) if record.status == APPLYING else 0
    with db.unscoped_transaction() as conn:
        conn.execute(
            f"INSERT INTO change_approval (id, owner_id, kind, step, {', '.join(fields)}) "
            f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(fields))}) "
            "ON CONFLICT (id) DO UPDATE SET step = EXCLUDED.step, " + ", ".join(f"{name} = EXCLUDED.{name}" for name in fields),
            (record.id, user_id or user_scope.get_current_user_id(), record.kind, step, *(getattr(record, name) for name in fields)),
        )
    return change_store.save(record, user_id)
