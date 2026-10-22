"""The guard rails: what an Azure change may touch and cost. Stored here, edited through `/v1/admin/guardrails`.

There is no `deletes_allowed` and no `max_deletes` (3 October 2026): deleting is not configurable. An
unknown key is refused rather than stored, so nobody can add one that a later reader might honour.
Empty `subscriptions` means nothing can change; empty `regions` means no region is allowed until the Settings
screen fills it from the inventory.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from cloud_executor import keys

GUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
NAME = re.compile(r"[A-Za-z0-9._()-]{1,90}")
REGION = re.compile(r"[a-z0-9]{3,30}")
TYPE = re.compile(r"[A-Za-z0-9_./]{3,120}")
DEFAULT_PATTERN = r"(CR|SR)-?[0-9]{4,10}"
MAX_REFERENCE = 40
_lock = threading.Lock()


def defaults(template_types: list[str] | None = None) -> dict[str, Any]:
    return {
        "subscriptions": [], "resource_groups": {"existing": [], "new_prefix": "rg-"}, "regions": [],
        "resource_types": sorted(template_types or []), "vm_sizes_max_vcpu": 8, "usd_month_per_change": 250,
        "usd_month_total": 1000, "low_risk_usd_month": 20, "max_resources": 40, "allow_free_code": False,
        "allow_unknown_price": False, "reference_pattern": DEFAULT_PATTERN, "document_valid_days": 14,
    }


def _path() -> str:
    return os.path.join(keys.state_dir(), "rails.json")


def _strings(value: Any, pattern: re.Pattern[str], what: str, limit: int = 500) -> list[str]:
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(v, str) and pattern.fullmatch(v) for v in value):
        raise ValueError(f"{what} must be a list of at most {limit} valid values.")
    return sorted({v.lower() if pattern is not NAME else v for v in value})


def _number(value: Any, what: str, low: float, high: float, whole: bool = False) -> Any:
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)) or not low <= value <= high:
        raise ValueError(f"{what} must be {'a whole ' if whole else 'a '}number from {low} to {high}.")
    return value


def validate_pattern(pattern: Any) -> str:
    """The pattern, or ValueError: it must compile, be at most 100 characters and not match the empty string."""
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 100:
        raise ValueError("reference_pattern must be 1 to 100 characters.")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"reference_pattern does not compile: {error}.") from error
    if compiled.fullmatch(""):
        raise ValueError("reference_pattern matches the empty string, so it would accept no number at all.")
    return pattern


def validate(body: Any, template_types: list[str] | None = None) -> dict[str, Any]:
    """A complete, clean set of rails from `body` (missing keys take the default), or ValueError naming the problem."""
    if not isinstance(body, dict):
        raise ValueError("The guard rails must be an object.")
    base = defaults(template_types)
    if set(body) - set(base):
        raise ValueError(f"Unknown guard rail: {sorted(set(body) - set(base))[0]}. Deleting is not configurable.")
    out = {**base, **body}
    out["subscriptions"] = _strings(out["subscriptions"], GUID, "subscriptions", 50)
    groups = out["resource_groups"]
    if not isinstance(groups, dict) or set(groups) - {"existing", "new_prefix"}:
        raise ValueError("resource_groups holds existing and new_prefix only.")
    prefix = groups.get("new_prefix", "rg-")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", prefix):
        raise ValueError("resource_groups.new_prefix must be 1 to 20 plain characters.")
    out["resource_groups"] = {"existing": sorted(set(_strings(groups.get("existing", []), NAME, "resource_groups.existing"))),
                              "new_prefix": prefix.lower()}
    out["regions"] = _strings(out["regions"], REGION, "regions", 100)
    out["resource_types"] = _strings(out["resource_types"], TYPE, "resource_types", 200)
    out["vm_sizes_max_vcpu"] = _number(out["vm_sizes_max_vcpu"], "vm_sizes_max_vcpu", 1, 128, True)
    for key in ("usd_month_per_change", "usd_month_total", "low_risk_usd_month"):
        out[key] = _number(out[key], key, 0, 1_000_000)
    out["max_resources"] = _number(out["max_resources"], "max_resources", 1, 200, True)
    out["document_valid_days"] = _number(out["document_valid_days"], "document_valid_days", 1, 30, True)
    for key in ("allow_free_code", "allow_unknown_price"):
        if not isinstance(out[key], bool):
            raise ValueError(f"{key} must be true or false.")
    out["reference_pattern"] = validate_pattern(out["reference_pattern"])
    return out


def load(template_types: list[str] | None = None) -> dict[str, Any]:
    """The stored rails, or the defaults. An unreadable file is the defaults (nothing can change), never an open door."""
    try:
        with open(_path(), encoding="utf-8") as handle:
            return validate(json.load(handle), template_types)
    except (OSError, ValueError):
        return defaults(template_types)


def save(body: Any, template_types: list[str] | None = None) -> dict[str, Any]:
    clean = validate(body, template_types)
    path = _path()
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            json.dump(clean, handle)
        os.replace(tmp, path)
    return clean


def reference_ok(reference: Any, rails: dict[str, Any]) -> bool:
    """A full, case-insensitive match of the stored pattern, at most 40 characters."""
    return (isinstance(reference, str) and 0 < len(reference.strip()) <= MAX_REFERENCE
            and re.fullmatch(rails["reference_pattern"], reference.strip(), re.IGNORECASE) is not None)
