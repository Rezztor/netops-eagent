"""Refresh device facts through the executor and turn them into proposals or conflicts (Phase 4).

Runs the read playbook `reads/inventory_facts` in check mode for each device, parses the
`EAGENT_FACTS` line, normalises it, stores an observation and asks the precedence engine what
each observed value means. Nothing here writes a device: values become one `update` proposal
per device or a conflict, and a person decides (ADR 0039/0040/0043).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from plugins._ea_inventory.helpers import proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError

SOURCE = "facts"
PLAYBOOK = "reads/inventory_facts"
PLATFORMS = ("ios", "iosxe", "nxos", "eos", "junos", "sonic", "sonic_community", "fortios", "panos")
CAP = 10
_LINE = re.compile(r"EAGENT_FACTS (\S+) (\{.*\})")


@dataclass
class RefreshResult:
    results: list[dict[str, Any]] = field(default_factory=list)
    batch_id: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def parse_facts(output: str) -> dict[str, dict[str, Any]]:
    """`{host: {"platform", "facts"}}` from the job output. The debug callback prints the line as a
    JSON string, so the quotes inside it arrive escaped; undo that when plain parsing fails."""
    found: dict[str, dict[str, Any]] = {}
    for host, blob in _LINE.findall(output or ""):
        for load in (lambda: json.loads(blob), lambda: json.loads(json.loads(f'"{blob}"'))):
            try:
                doc = load()
            except ValueError:
                continue
            if isinstance(doc, dict) and isinstance(doc.get("facts"), dict):
                found[host] = doc
                break
    return found


def _get(plan: Any, key: str) -> Any:
    return plan[key] if isinstance(plan, dict) else getattr(plan, key)


def _select(names: list[str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """(devices to refresh, skipped results, how many eligible ones the cap left out)."""
    active = {d["name"].lower(): d for d in store.list_devices(status="active")}
    skipped: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []
    for name in (names if names is not None else sorted(active)):
        d = active.get(str(name).strip().lower())
        reason = (
            "no such active device." if not d
            else f"platform {d['platform']} has no facts collection yet." if d["platform"] not in PLATFORMS
            else ""
        )
        if reason and names is not None:
            skipped.append({"device": str(name), "status": "skipped", "error": reason, "platform": d["platform"] if d else ""})
        elif not reason:
            todo.append(d)
    if names is not None:
        skipped += [
            {"device": d["name"], "status": "skipped", "error": f"over the {CAP}-device cap; run again.", "platform": d["platform"]}
            for d in todo[CAP:]
        ]
    return todo[:CAP], skipped, 0 if names is not None else max(0, len(todo) - CAP)


def refresh_facts(
    devices: list[str] | None, *, actor: str, dry_run: bool = False, agent: Any = None,
) -> RefreshResult:
    """Raises InventoryError (nothing created) when the executor cannot be reached at all."""
    from plugins._ea_ansible.helpers import executor_client, failures
    from plugins._ea_inventory.helpers import conflicts, precedence
    from plugins._ea_inventory.helpers.facts import normalisers

    todo, results, capped = _select(devices)
    if todo:
        try:
            commit = str(executor_client.health(agent).get("catalogue_commit") or "")
        except executor_client.ExecutorError as error:
            raise InventoryError(str(error)) from error
    at = _dt.datetime.now(_dt.timezone.utc)
    batch_id = str(uuid.uuid4())
    made = 0
    for device in todo:
        name = device["name"]
        out: dict[str, Any] = {
            "device": name, "status": "error", "platform": device["platform"], "fields_seen": [],
            "proposals": 0, "conflicts": 0, "unchanged": 0, "pinned": 0, "warnings": [],
        }
        results.append(out)
        try:
            job = executor_client.run(agent, PLAYBOOK, commit, {}, limit=name, check_mode=True)
            if not job.succeeded:
                out["error"] = (f"the facts job {job.job_id} ended {job.status} (exit code {job.exit_code}). "
                                f"{failures.classify(job.output)['cause']}")
                continue
            payload = parse_facts("\n".join([job.output, *(str(e.get("msg") or "") for e in job.events)])).get(name)
            if payload is None:
                out["error"] = f"job {job.job_id} printed no facts line for {name}; is it in the executor inventory (sync it)?"
                continue
            norm = normalisers.normalise_facts(device["platform"], payload["facts"])
            out["warnings"] += list(norm.warnings)
            if norm.hostname and norm.hostname != name.lower():
                out["warnings"].append(f"the device reports hostname '{norm.hostname}', not '{name}'; nothing changed because of it.")
            flat = precedence.flatten(norm.fields)
            out["fields_seen"] = sorted(flat)
            pins, kept = conflicts.load_pins_and_kept(device["id"])
            plan = precedence.plan(device, flat, SOURCE, pins, kept)
            propose, found = _get(plan, "propose"), _get(plan, "conflicts")
            out.update(
                conflicts=len(found), unchanged=len(_get(plan, "unchanged")), pinned=len(_get(plan, "pinned")),
                proposals=1 if propose else 0,
            )
            evidence = {"source": SOURCE, "job_id": job.job_id, "observed_at": at.isoformat(), "batch_id": batch_id}
            if not dry_run:
                store.record_sync(SOURCE, [(device["id"], name, {**norm.fields, "hostname": norm.hostname, "job_id": job.job_id})], [device["id"]], at)
                if found:
                    conflicts.upsert_conflicts(device["id"], SOURCE, found, evidence, at)
                if propose and proposals.list_proposals("pending", 1, 0, device["id"], SOURCE)[1]:
                    out["proposals"] = 0
                    out["warnings"].append("a facts request for this device is already pending.")
                elif propose:
                    try:
                        proposals.propose("update", device["id"], precedence.to_fields(device, propose), SOURCE, evidence, actor)
                    except InventoryError as error:
                        out["proposals"] = 0
                        if not str(error).startswith("Nothing would change"):
                            out["error"] = str(error)
                            continue
            made += out["proposals"]
            out["status"] = "ok"
        except Exception as error:  # noqa: BLE001 - one device failing must not stop the others
            out["error"] = str(error) or type(error).__name__
    counts = {
        "devices": len(results), "ok": 0, "error": 0, "skipped": 0, "proposals": 0, "conflicts": 0,
        "unchanged": 0, "pinned": 0, "capped": capped,
    }
    for r in results:
        counts[r["status"]] += 1
        for k in ("proposals", "conflicts", "unchanged", "pinned"):
            counts[k] += r.get(k, 0)
    return RefreshResult(results, batch_id if made and not dry_run else None, counts)
