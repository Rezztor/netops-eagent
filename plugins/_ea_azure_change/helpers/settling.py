"""Settle the Azure changes an interrupted apply left `applying`.

An apply waits on the executor's job inside one tool call. If the application restarts meanwhile, the executor still finishes
the job and nothing asks it again. Once a minute (`extensions/python/job_loop/_65_azure_change_settle.py`) this reads the state of
every change that is `applying` with a job and settles it as the tool would have: status, verify, `cloud_managed`, the inventory
pull and the as-applied document (`records.settle`; `sets.settle` for a change set).

A set is settled at the step that was running and **never continued here**: continuing needs the engineer. A step that applied
leaves the set `applying` with the next steps to run (the engineer's `azure_apply` goes on, under the approval they gave); a step
that did not apply ends the set stopped at that step, as in `sets.run`. Quiet: a plugin that is off, an executor that is not
configured or does not answer, or a record that cannot be settled changes nothing and raises nothing.
"""

from __future__ import annotations

import logging

from helpers import user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLYING
from plugins._ea_azure_change.helpers import executor_client, records, sets

log = logging.getLogger(__name__)

#: Records looked at (one executor call each) per tick, oldest first; the rest wait for the next minute.
PER_TICK = 3


def _settle(record) -> bool:
    """Ask the executor about one applying record and settle it if its job ended. True when it ended."""
    owner = record.requested_by
    if sets.is_set(record):
        return sets.settle(None, record, owner) is not None
    if record.kind == "azure_action":  # as the action tool does: the resource was not made by the product, so none is listed
        job = executor_client.job(None, record.job_id)
        if job.get("status") not in executor_client.TERMINAL:
            return False
        records.finish(record, {**job, "changed_ids": []}, owner)
        return True
    return records.settle(None, record, owner) is not None


def settle_open() -> int:
    """Settle up to `PER_TICK` applying records, each as its owner. Blocking. Returns how many ended."""
    if not records.enabled() or not executor_client.is_configured():
        return 0
    looked = ended = 0
    for user_id in user_scope.list_user_ids():
        with user_scope.user_scope(user_id):  # a background run has no request: the owner's scope is pushed explicitly
            for record in reversed(change_store.list_records(user_id)):  # oldest first
                if looked >= PER_TICK:
                    return ended
                waiting = any(s.get("status") == "applying" for s in record.steps) if sets.is_set(record) else bool(record.job_id)
                if record.kind not in records.KINDS or record.status != APPLYING or not waiting or records.is_in_flight(record.id):
                    continue  # not waiting on a job, or a call in this process is: it settles its own
                looked += 1
                try:
                    ended += _settle(record)
                except executor_client.ExecutorError as error:  # unreachable or refusing: try again next minute
                    log.info("azure change %s: not settled now (%s)", record.id, type(error).__name__)
                except Exception as error:  # noqa: BLE001 - housekeeping must not stop the loop, and one bad record must not stop the rest
                    log.error("azure change %s: could not be settled (%s)", record.id, type(error).__name__)
    return ended
