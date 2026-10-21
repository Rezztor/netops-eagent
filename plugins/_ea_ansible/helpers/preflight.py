"""Everything that can be checked before a person is asked to approve a read.

Runs from the application (inventory facts, catalogue) and from inside the
executor (host known, credential present, TCP reachable) so the card only
appears for a request that has a fair chance of working. Read-only; no job.
"""

from __future__ import annotations

from typing import Any

from plugins._ea_ansible.helpers import executor_client, network_list


def _check(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"label": label, "ok": bool(ok), "detail": detail}


def run_preflight(agent: Any | None, devices: list[dict], entry: Any) -> dict[str, Any]:
    # Lazy: a pure module, but it belongs to the inventory plugin.
    from plugins._ea_inventory.helpers.executor_sync import PLATFORM_VARS

    checks: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    read_commands = getattr(entry, "read_commands", None) or {}

    try:
        remote = executor_client.preflight(agent, [d["name"] for d in devices]) if devices else {}
    except executor_client.ExecutorError as error:
        remote = {}
        checks.append(_check("Executor answers", False, str(error)))

    listing: dict[str, Any] | None = None  # fetched once, and only when a connect failed with a reason

    for device in devices:
        name, platform = device["name"], device.get("platform") or ""
        seen = remote.get(name) or {}
        site = device.get("site") or {}
        facts.append({
            "name": name,
            "mgmt_ip": device.get("mgmt_ip") or "",
            "site": site.get("name", "") if isinstance(site, dict) else str(site),
            "platform": platform,
            "credential": seen.get("credential") or "",
            "last_seen": device.get("last_seen_at") or "",
            "network": f"{seen['network']}, routed" if seen.get("network") and seen.get("routed") else seen.get("network") or "",
        })
        want = PLATFORM_VARS.get(platform) or {}
        port = seen.get("port") or 22
        address = seen.get("address") or device.get("mgmt_ip")
        why_unreachable = f"TCP connect to {address}:{port} failed"
        if seen.get("reason") and not seen.get("reachable"):
            if listing is None:
                try:
                    listing = executor_client.networks(agent)
                except executor_client.ExecutorError:
                    listing = network_list.parse(None)
            # With no list there is no sentence, and today's text stays.
            why_unreachable = network_list.sentence(address, port, seen, listing) or why_unreachable
        rows = (
            ("active in Inventory", device.get("status") == "active", f"status is {device.get('status')!r}"),
            ("platform supported", platform in entry.platforms and bool(read_commands.get(platform)),
             f"{entry.name} has no commands for {platform or 'an unset platform'}"),
            ("known to the executor", seen.get("in_inventory"), "not in the executor inventory; sync Inventory"),
            ("credential present", seen.get("credential_present"), "no credential is stored for this device"),
            ("reachable", seen.get("reachable"), why_unreachable),
            ("connection matches platform",
             bool(want) and seen.get("network_os") == want.get("ansible_network_os")
             and seen.get("connection") == want.get("ansible_connection"),
             f"executor has {seen.get('network_os')!r} over {seen.get('connection')!r}; Inventory sync is stale"),
        )
        checks += [_check(f"{name}: {label}", ok, "" if ok else why) for label, ok, why in rows]

    return {"ok": all(c["ok"] for c in checks), "checks": checks, "devices": facts}
