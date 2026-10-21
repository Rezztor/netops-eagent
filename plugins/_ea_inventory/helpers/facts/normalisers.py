"""Vendor fact-module output -> Device fields. Pure, never raises.

Shapes are taken from the vendors' module documentation and are unverified against live
devices except where a fixture says otherwise (see tests/fixtures/facts). Only the
platform's own keys are read; every value goes through `normalise.normalise_fields`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plugins._ea_inventory.helpers.normalise import InventoryError, normalise_fields

VENDORS = {
    "ios": "Cisco", "iosxe": "Cisco", "nxos": "Cisco", "eos": "Arista", "junos": "Juniper",
    "fortios": "Fortinet", "panos": "Palo Alto", "sonic": "SONiC", "sonic_community": "SONiC",
}
ROLES = ("primary", "secondary", "standalone", "unknown")


@dataclass
class NormalisedFacts:
    fields: dict[str, Any] = field(default_factory=dict)
    hostname: str = ""
    warnings: list[str] = field(default_factory=list)


def _get(facts: dict, key: str) -> Any:
    """`ansible_net_<key>`, or `net_<key>` (some modules drop the prefix)."""
    for k in (f"ansible_net_{key}", f"net_{key}"):
        if facts.get(k) not in (None, "", [], {}):
            return facts[k]
    return None


def _str(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _dig(d: Any, *path: str) -> Any:
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def _results(d: Any) -> Any:
    """Fortinet wraps payloads in `results`; accept either level."""
    return d.get("results", d) if isinstance(d, dict) else None


def _ha(role: str = "", group: str = "", peer: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if group:
        out["ha_group"] = group
    custom = {k: v for k, v in (("ha_role", role), ("ha_peer", peer)) if v}
    if custom:
        out["custom"] = custom
    return out


def _generic(facts: dict, platform: str) -> tuple[dict[str, Any], str]:
    """ios, iosxe, nxos, eos, junos, sonic: the `ansible_net_*` family."""
    raw = {"model": _get(facts, "model"), "serial": _get(facts, "serialnum") or _get(facts, "serial"),
           "software_version": _get(facts, "version")}
    out: dict[str, Any] = {k: _str(v) for k, v in raw.items() if _str(v)}
    serial = out.get("serial", "")
    stacked = _get(facts, "stacked_serialnums")  # cisco.ios: stack members
    if isinstance(stacked, list) and len(stacked) > 1:
        peers = [s for s in map(_str, stacked) if s and s.upper() != serial.upper()]
        out.update(_ha("unknown", "", ", ".join(peers)))
    members = _get(facts, "multi_ne")  # junos virtual chassis: {member: {serialnum, ...}}
    if platform == "junos" and isinstance(members, dict) and len(members) > 1:
        serials = [_str(_dig(m, "serialnum") or _dig(m, "serialnumber")) for m in members.values()]
        peers = [s for s in serials if s and s.upper() != serial.upper()]
        out.update(_ha("unknown", _str(_get(facts, "hostname")).lower(), ", ".join(peers)))
    return out, _str(_get(facts, "hostname"))


def _fortios(facts: dict) -> tuple[dict[str, Any], str]:
    status = facts.get("system_status")
    res = _results(status) or {}
    out: dict[str, Any] = {}
    # `model` is the model as the unit reports it (FGT60F); `model_name` alone is just "FortiGate".
    model = _str(res.get("model") or "-".join(filter(None, (_str(res.get("model_name")), _str(res.get("model_number"))))))
    serial = _str(_dig(status, "serial") or res.get("serial"))
    version = _str(_dig(status, "version") or res.get("version")).lstrip("vV")
    for k, v in (("model", model), ("serial", serial), ("software_version", version)):
        if v:
            out[k] = v
    ha = _results(facts.get("system_ha")) or {}  # cmdb system/ha: mode, group-name
    mode = _str(ha.get("mode")).lower()
    peers = _results(facts.get("system_ha-peer"))
    peers = peers if isinstance(peers, list) else []
    if mode == "standalone":
        out.update(_ha("standalone"))
    elif mode:
        peer = _str(_dig(peers[0] if peers else None, "serial_no"))
        # peers lists the *other* units; a peer flagged primary makes this unit secondary
        flagged = [p.get("primary") for p in peers if isinstance(p, dict) and "primary" in p]
        role = "unknown" if not flagged else ("secondary" if any(flagged) else "primary")
        out.update(_ha(role, _str(ha.get("group-name")), peer))
    return out, _str(res.get("hostname"))


def _panos(facts: dict) -> tuple[dict[str, Any], str]:
    out = {k: _str(_get(facts, g)) for k, g in
           (("model", "model"), ("serial", "serial"), ("software_version", "version")) if _str(_get(facts, g))}
    enabled = _get(facts, "ha_enabled")
    state = _str(_get(facts, "ha_localstate")).lower()
    if enabled is False or str(enabled).lower() == "false":
        out.update(_ha("standalone"))
    elif enabled is True or str(enabled).lower() == "true":
        role = ("primary" if state in ("active", "active-primary") else
                "secondary" if state in ("passive", "active-secondary") else "unknown")
        out.update(_ha(role, "", _str(_get(facts, "ha_peer_serial"))))  # peer key unverified
    return out, _str(_get(facts, "hostname"))


def normalise_facts(platform: str, facts: dict) -> NormalisedFacts:
    result = NormalisedFacts()
    try:
        if platform not in VENDORS:
            result.warnings.append(f"No facts normaliser for platform {platform!r}.")
            return result
        if not isinstance(facts, dict) or not facts:
            result.warnings.append("No facts returned; nothing to record.")
            return result
        raw, host = (_fortios(facts) if platform == "fortios" else _panos(facts) if platform == "panos"
                     else _generic(facts, platform))
        if not raw and not host:
            result.warnings.append("Facts held none of the expected keys; nothing to record.")
            return result
        result.hostname = host.lower()
        for name, value in ({"vendor": VENDORS[platform], **raw} if raw else {}).items():
            try:
                clean = normalise_fields({name: value})[name]
            except InventoryError as exc:
                result.warnings.append(f"Ignored {name}: {exc}")
                continue
            if clean not in ("", {}):
                result.fields[name] = clean
        for name in ("model", "serial", "software_version"):
            if name not in result.fields and not platform.startswith("sonic"):
                result.warnings.append(f"Facts did not include {name}.")
    except Exception as exc:  # never raises: the caller reports per-device results
        result.fields, result.hostname = {}, ""
        result.warnings.append(f"Could not read facts: {type(exc).__name__}.")
    return result
