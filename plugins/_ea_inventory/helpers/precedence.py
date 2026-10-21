"""Which source may change which field, decided without touching anything (ADR 0043). Pure.

Automatic sources have a rank (`facts` 2, `solarwinds` 1); everything else (`manual`,
`extraction`, `agent`, unknown) is *human*. An automatic source may overwrite a value
only if it is empty or was last set by the same or a lower-ranked automatic source. A
differing human value is a *conflict* for a person to settle, never an overwrite.
Custom keys are named `custom.<key>`; a nested `custom` dict in `observed` is flattened.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _f
from typing import Any

from plugins._ea_inventory.helpers.normalise import InventoryError, normalise_ip, normalise_serial

RANK = {"facts": 2, "solarwinds": 1}
MAY_TOUCH = {
    "facts": ("vendor", "model", "serial", "part_number", "base_mac", "software_version", "ha_group", "custom.ha_role", "custom.ha_peer"),
    "solarwinds": ("vendor", "model", "software_version", "mgmt_ip", "platform", "serial"),
}


@dataclass
class PlanResult:
    propose: dict[str, Any] = _f(default_factory=dict)
    conflicts: list[dict[str, Any]] = _f(default_factory=list)
    unchanged: list[str] = _f(default_factory=list)
    pinned: list[str] = _f(default_factory=list)
    outranked: list[str] = _f(default_factory=list)
    ignored: list[str] = _f(default_factory=list)


def current_value(device: dict[str, Any], field: str) -> Any:
    if field.startswith("custom."):
        return (device.get("custom") or {}).get(field[7:])
    return device.get(field)


def current_source(device: dict[str, Any], field: str) -> str:
    """Provenance is per top-level field, so every `custom.*` key shares `custom`'s."""
    return str(((device.get("field_sources") or {}).get(field.split(".")[0]) or {}).get("source") or "")


def _empty(field: str, value: Any) -> bool:
    return value in ("", None) or (field == "platform" and value == "other")


def norm(field: str, value: Any) -> str:
    text = str(value if value is not None else "").strip()
    try:
        if field == "serial":
            return normalise_serial(text)
        if field == "mgmt_ip":
            return normalise_ip(text)
    except InventoryError:
        return text
    return text.casefold() if field == "vendor" else text


def flatten(observed: dict[str, Any]) -> dict[str, Any]:
    flat = {k: v for k, v in observed.items() if k != "custom"}
    if isinstance(observed.get("custom"), dict):
        flat.update({f"custom.{k}": v for k, v in observed["custom"].items()})
    return flat


def to_fields(device: dict[str, Any], propose: dict[str, Any]) -> dict[str, Any]:
    """`plan().propose` as proposal fields: `custom.<key>` entries merge into one `custom` dict."""
    out = {k: v for k, v in propose.items() if not k.startswith("custom.")}
    keys = {k[7:]: v for k, v in propose.items() if k.startswith("custom.")}
    if keys:
        out["custom"] = {**(device.get("custom") or {}), **keys}
    return out


def plan(
    device: dict[str, Any], observed: dict[str, Any], source: str, pins: set[tuple[str, str]],
    kept: set[tuple[str, str, str]] = frozenset(),
) -> PlanResult:
    """`pins` are `(field, source)`; `kept` are `(field, source, proposed value as text)`."""
    out = PlanResult()
    allowed = MAY_TOUCH.get(source, ())
    for f, proposed in flatten(observed).items():
        if f not in allowed or _empty(f, proposed):
            continue
        now = current_value(device, f)
        empty = _empty(f, now)
        if not empty and norm(f, now) == norm(f, proposed):
            out.unchanged.append(f)
        elif (f, source) in pins:
            out.pinned.append(f)
        elif empty:
            out.propose[f] = proposed
        elif source == "solarwinds" and f == "serial":
            continue  # only ever fills an empty serial
        else:
            prov = current_source(device, f)
            if prov == source or (prov in RANK and RANK[prov] < RANK.get(source, 0)):
                out.propose[f] = proposed
            elif prov in RANK:
                out.outranked.append(f)
            elif (f, source, str(proposed).strip()) in kept:
                out.ignored.append(f)
            else:
                out.conflicts.append({"field": f, "current": now, "proposed": proposed, "current_source": prov})
    return out
