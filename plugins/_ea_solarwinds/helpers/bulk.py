"""Read every node from SolarWinds, for the inventory. Read-only, all-or-nothing.

Runs the internal `nodes_page` / `node_serials_*_page` capabilities (fixed
templates, bound parameters -- see `helpers/capabilities.py`) with keyset paging
on NodeID. Any page that fails raises `BulkFetchError`, so a caller never sees a
partial list. The one tolerated failure is a column or entity this instance does
not have: it is dropped, named in `missing_columns`, and the fetch carries on.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from plugins._ea_solarwinds.helpers import capabilities, client, policy

_RATE_KEY = "inventory_bulk"  # one shared bucket in policy's ledger, not a chat's
_RATE_WAIT_SECONDS = 5
_RATE_WAITS = 14  # ~70s: a full minute window always clears

_SERIAL_SOURCES = (
    ("node_serials_page", "ServiceTag", "Orion.HardwareHealth.HardwareInfo"),
    ("node_serials_ncm_page", "SerialNumber", "Cirrus.EntityPhysical"),
)
_STATUS = {1: "up", 2: "down", 3: "warning", 9: "unmanaged", 12: "unreachable", 14: "critical"}


class BulkFetchError(client.SolarWindsError):
    """The fetch could not complete; nothing it read should be used."""


@dataclass(slots=True)
class NodesResult:
    nodes: list[dict[str, Any]]
    complete: bool
    truncated: bool
    missing_columns: list[str] = field(default_factory=list)


def _run(name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    built = capabilities.build(name, args, internal=True)
    for wait in range(_RATE_WAITS + 1):
        try:
            policy.check_and_record(_RATE_KEY)
            break
        except policy.RateLimited as error:
            if wait == _RATE_WAITS:
                raise BulkFetchError(str(error)) from error
            time.sleep(_RATE_WAIT_SECONDS)
    return client.query(built.swql, built.params)


def _nodes_page(after: int, page: int, omit: list[str], missing: list[str]) -> list[dict[str, Any]]:
    while True:
        try:
            return _run("nodes_page", {"after": after, "page": page, "omit": omit})
        except client.QueryRefused as error:
            # A 400 naming one optional column: drop it, say so, retry the page.
            column = next(
                (c for c in capabilities.NODE_OPTIONAL_COLUMNS
                 if c not in omit and error.status == 400
                 and re.search(rf"\b{c}\b", str(error), re.IGNORECASE)),
                None,
            )
            if column is None:
                raise BulkFetchError(f"SolarWinds node read failed: {error}") from error
            omit.append(column)
            missing.append(f"Orion.Nodes.{column}")
        except client.SolarWindsError as error:
            raise BulkFetchError(f"SolarWinds node read failed: {error}") from error


def _serials(after: int, upto: int, live: list, missing: list[str]) -> dict[int, str]:
    found: dict[int, str] = {}
    for source in list(live):
        name, column, entity = source
        try:
            rows = _run(name, {"after": after, "upto": upto})
        except (client.CapabilityUnavailable, client.QueryRefused) as error:
            if isinstance(error, client.QueryRefused) and error.status != 400:
                raise BulkFetchError(f"SolarWinds serial read failed: {error}") from error
            live.remove(source)
            missing.append(f"{entity}.{column}")
            continue
        except client.SolarWindsError as error:
            raise BulkFetchError(f"SolarWinds serial read failed: {error}") from error
        for row in rows:
            serial = str(row.get(column) or "").strip()
            if serial and isinstance(row.get("NodeID"), int):
                found.setdefault(row["NodeID"], serial)
    return found


def _iso(value: Any) -> str | None:
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(value or "").strip())
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def _node(row: dict[str, Any], serial: str | None) -> dict[str, Any]:
    def text(key: str) -> str:
        return str(row.get(key) or "").strip()

    status = row.get("Status")
    return {
        "node_id": str(row["NodeID"]),
        "caption": text("Caption"),
        "dns": text("DNS"),
        "sys_name": text("SysName"),
        "ip_address": text("IPAddress"),
        "vendor": text("Vendor"),
        "machine_type": text("MachineType"),
        "software_version": text("IOSVersion"),
        "sys_object_id": text("SysObjectID"),
        "location": text("Location"),
        "status": _STATUS.get(status, str(status if status is not None else "")),
        "serial": serial,
        "last_poll": _iso(row.get("LastSystemUpTimePollUtc")),
    }


def fetch_nodes(max_nodes: int = 2000, page_size: int = 200) -> NodesResult:
    nodes: list[dict[str, Any]] = []
    omit: list[str] = []
    missing: list[str] = []
    live = list(_SERIAL_SOURCES)
    after = 0
    truncated = False
    while True:
        at_cap = len(nodes) >= max_nodes
        want = 1 if at_cap else min(page_size, max_nodes - len(nodes))  # at the cap: probe for one more
        rows = _nodes_page(after, want, omit, missing)
        if at_cap:
            truncated = bool(rows)
            break
        ids = [row.get("NodeID") for row in rows]
        if not all(isinstance(i, int) for i in ids) or ids != sorted(set(ids)) or (ids and ids[0] <= after):
            raise BulkFetchError("SolarWinds returned node rows out of order or without a NodeID.")
        if not rows:
            break
        serials = _serials(after, ids[-1], live, missing)
        nodes.extend(_node(row, serials.get(row["NodeID"])) for row in rows)
        after = ids[-1]
        if len(rows) < want:
            break
    return NodesResult(nodes, not truncated, truncated, list(dict.fromkeys(missing)))
