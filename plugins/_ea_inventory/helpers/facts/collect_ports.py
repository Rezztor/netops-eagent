"""Refresh a device's ports and parts through the executor (ADR 0045, 0049).

Runs the read playbook `reads/inventory_ports` in check mode, parses the `EAGENT_PORTS` line and
replaces the device's stored snapshot. Observed data needs no approval and touches no device field.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

from plugins._ea_inventory.helpers import ports, store
from plugins._ea_inventory.helpers.facts import collect, ports_parse
from plugins._ea_inventory.helpers.normalise import InventoryError

PLAYBOOK = "reads/inventory_ports"
PLATFORMS = ("ios", "nxos", "eos", "junos", "fortios", "panos", "sonic_community")
_LINE = re.compile(r"EAGENT_PORTS (\S+) (\{.*\})")


def parse_output(output: str) -> dict[str, dict[str, Any]]:
    """`{host: payload}`. The debug callback escapes the quotes inside the line; undo that when plain parsing fails."""
    found: dict[str, dict[str, Any]] = {}
    for host, blob in _LINE.findall(output or ""):
        for load in (lambda: json.loads(blob), lambda: json.loads(json.loads(f'"{blob}"'))):
            try:
                doc = load()
            except ValueError:
                continue
            if isinstance(doc, dict):
                found[host] = doc
                break
    return found


def refresh_ports(devices: list[str] | None, *, dry_run: bool = False, agent: Any = None) -> dict[str, Any]:
    """Raises InventoryError (nothing changed) when the executor cannot be reached at all."""
    from plugins._ea_ansible.helpers import executor_client, failures

    active = {d["name"].lower(): d for d in store.list_devices(status="active")}
    skipped: list[dict[str, str]] = []
    todo: list[dict[str, Any]] = []
    for name in devices if devices is not None else sorted(active):
        d = active.get(str(name).strip().lower())
        reason = (
            "no such active device." if not d
            else f"platform {d['platform']} has no ports collection yet." if d["platform"] not in PLATFORMS else ""
        )
        if not reason:
            todo.append(d)
        elif devices is not None:
            skipped.append({"device": str(name), "error": reason})
    skipped += [{"device": d["name"], "error": f"over the {collect.CAP}-device cap; run again."} for d in todo[collect.CAP:]]
    todo = todo[:collect.CAP]
    if todo:
        try:
            commit = str(executor_client.health(agent).get("catalogue_commit") or "")
        except executor_client.ExecutorError as error:
            raise InventoryError(str(error)) from error
    out: dict[str, Any] = {"refreshed": 0, "failed": [], "skipped": len(skipped), "skipped_detail": skipped, "interfaces": 0, "components": 0}
    at = _dt.datetime.now(_dt.timezone.utc)
    for device in todo:
        name = device["name"]
        try:
            job = executor_client.run(agent, PLAYBOOK, commit, {}, limit=name, check_mode=True)
            if not job.succeeded:
                raise InventoryError(f"the ports job {job.job_id} ended {job.status} (exit code {job.exit_code}). "
                                     f"{failures.classify(job.output)['cause']}")
            payload = parse_output("\n".join([job.output, *(str(e.get("msg") or "") for e in job.events)])).get(name)
            if payload is None:
                raise InventoryError(f"job {job.job_id} printed no ports line for {name}; is it in the executor inventory (sync it)?")
            parsed = ports_parse.parse(device["platform"], payload)
            if not parsed.interfaces:  # an empty or partial read must not wipe a good snapshot (and its uplink marks)
                raise InventoryError("the device answered but nothing in it could be read as ports.")
            n_if, n_part = (len(parsed.interfaces), len(parsed.components)) if dry_run else ports.replace_snapshot(device["id"], parsed, at)
            out["interfaces"] += n_if
            out["components"] += n_part
            out["refreshed"] += 1
        except Exception as error:  # noqa: BLE001 - one device failing must not stop the others
            out["failed"].append({"device": name, "error": str(error) or type(error).__name__})
    return out
