"""Validation and normalisation of device fields. Pure: no database, no I/O.

Uniqueness (name, serial, management IP) needs the database and lives in
`store.py`; everything that can be decided from the value alone is decided here.
Unset text is `""`, an unset `place_id` is `None`; the store turns the empty serial
and management IP into NULL so placeholders never collide under a unique index.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import re
from typing import Any

from plugins._ea_inventory.helpers import places  # places imports this module back: only used inside functions

PLATFORMS = (
    "ios", "iosxe", "nxos", "eos", "junos", "fortios", "panos", "sonic",
    "sonic_community", "other",
)
STATUSES = ("active", "planned", "staged", "offline", "spare", "faulty", "decommissioned")
DEVICE_TYPES = ("router", "switch", "firewall", "wireless_controller", "access_point", "load_balancer", "other")
ROLE_SUGGESTIONS = ("core", "distribution", "access", "edge", "wan", "datacentre")
SOURCES = ("manual", "solarwinds", "extraction", "agent", "facts")

#: Fields a proposal may carry. Anything else is refused, never ignored.
FIELDS = (
    "name", "role", "vendor", "platform", "model", "serial", "asset_tag", "status",
    "place_id", "mgmt_ip", "software_version", "ha_group", "tags", "custom",
    "device_type", "part_number", "base_mac", "rack_unit", "u_height",
    "department", "custodian", "support_contact", "support_contract",
    "purchase_date", "warranty_expiry", "support_expiry", "eol_date",
)
#: Text fields with no rule beyond trimming and the length cap.
TEXT_FIELDS = (
    "part_number", "department", "custodian", "support_contact", "support_contract",
)
DATE_FIELDS = ("purchase_date", "warranty_expiry", "support_expiry", "eol_date")
INT_RANGES = {"rack_unit": (1, 60), "u_height": (1, 20)}
DEFAULTS: dict[str, Any] = {
    "name": "", "role": "", "vendor": "", "platform": "other", "model": "", "serial": "",
    "asset_tag": "", "status": "planned", "place_id": None, "mgmt_ip": "",
    "software_version": "", "ha_group": "", "tags": [], "custom": {},
    "device_type": "other", "rack_unit": None, "u_height": None, "base_mac": "",
    **{f: "" for f in TEXT_FIELDS}, **{f: None for f in DATE_FIELDS},
}

MAX_TEXT = 256
MAX_TAGS = 20
MAX_CUSTOM_KEYS = 50
_NAME = re.compile(r"^[a-z0-9._-]{1,128}$")
_PLACEHOLDER_SERIALS = {"N/A", "NA", "NONE", "UNKNOWN", "NULL", "-"}
# Same idea as the executor's FORBIDDEN_VARS, as substrings: custom fields are
# free-form, so a secret would otherwise land in a table every viewer can read.
_SECRET_WORDS = ("password", "passwd", "secret", "token", "community", "key")


class InventoryError(ValueError):
    """Refused. The message is written to be shown to the person who asked."""


def _text(field: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InventoryError(f"{field} must be text.")
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise InventoryError(f"{field} is longer than {MAX_TEXT} characters.")
    return value


def normalise_name(value: Any) -> str:
    name = _text("name", value).lower()
    if not name:
        raise InventoryError("name is required.")
    if not _NAME.match(name):
        raise InventoryError("name may use only a-z, 0-9, '.', '_' and '-' (max 128).")
    return name


def normalise_serial(value: Any) -> str:
    serial = _text("serial", value).upper()
    if serial in _PLACEHOLDER_SERIALS or (len(serial) >= 4 and len(set(serial)) == 1):
        return ""
    return serial


def normalise_ip(value: Any) -> str:
    text = _text("mgmt_ip", value)
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise InventoryError(f"mgmt_ip {text!r} is not a valid IPv4 or IPv6 address.") from None


_MAC = re.compile(
    r"[0-9a-f]{2}(:[0-9a-f]{2}){5}|[0-9a-f]{2}(-[0-9a-f]{2}){5}|[0-9a-f]{4}(\.[0-9a-f]{4}){2}|[0-9a-f]{12}", re.IGNORECASE
)


def normalise_mac(value: Any) -> str:
    text = _text("base_mac", value)
    if not text:
        return ""
    if not _MAC.fullmatch(text):
        raise InventoryError(f"base_mac {text!r} is not a MAC address (aa:bb:cc:dd:ee:ff, aabb.ccdd.eeff or 12 hex digits).")
    digits = re.sub(r"[:.-]", "", text).lower()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def normalise_date(field: str, value: Any) -> str | None:
    text = _text(field, value)
    if not text:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError
        return _dt.date.fromisoformat(text).isoformat()
    except ValueError:
        raise InventoryError(f"{field} must be a date written YYYY-MM-DD.") from None


def normalise_int(field: str, value: Any) -> int | None:
    if value in (None, ""):
        return None
    low, high = INT_RANGES[field]
    if isinstance(value, bool) or not isinstance(value, (int, str)) or (isinstance(value, str) and not value.strip().isdigit()):
        raise InventoryError(f"{field} must be a whole number from {low} to {high}.")
    number = int(value)
    if not low <= number <= high:
        raise InventoryError(f"{field} must be a whole number from {low} to {high}.")
    return number


def _choice(field: str, value: Any, allowed: tuple[str, ...]) -> str:
    text = _text(field, value).lower()
    if text not in allowed:
        raise InventoryError(f"{field} must be one of: {', '.join(allowed)}.")
    return text


def normalise_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise InventoryError("tags must be a list of text.")
    tags: list[str] = []
    for item in value:
        tag = _text("tag", item).lower()
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise InventoryError(f"at most {MAX_TAGS} tags.")
    return tags


def normalise_custom(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InventoryError("custom must be an object.")
    if len(value) > MAX_CUSTOM_KEYS:
        raise InventoryError(f"custom may hold at most {MAX_CUSTOM_KEYS} keys.")
    for key in value:
        if not isinstance(key, str) or not key.strip():
            raise InventoryError("custom keys must be non-empty text.")
        if any(word in key.lower() for word in _SECRET_WORDS):
            raise InventoryError(
                f"custom key {key!r} looks like a secret. Credentials do not belong in the "
                "inventory; store them as a credential on the executor."
            )
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        raise InventoryError("custom must be JSON-serialisable.") from None
    return dict(value)


def normalise_fields(fields: Any) -> dict[str, Any]:
    """Validate the fields given; absent ones stay absent. Unknown ones are refused."""
    if not isinstance(fields, dict):
        raise InventoryError("fields must be an object.")
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise InventoryError(f"Unknown field(s): {', '.join(map(str, unknown))}.")
    out: dict[str, Any] = {}
    for field, value in fields.items():
        if field == "name":
            out[field] = normalise_name(value)
        elif field == "serial":
            out[field] = normalise_serial(value)
        elif field == "mgmt_ip":
            out[field] = normalise_ip(value)
        elif field == "platform":
            out[field] = _choice(field, value, PLATFORMS)
        elif field == "status":
            out[field] = _choice(field, value, STATUSES)
        elif field == "device_type":
            out[field] = _choice(field, value, DEVICE_TYPES)
        elif field == "base_mac":
            out[field] = normalise_mac(value)
        elif field in DATE_FIELDS:
            out[field] = normalise_date(field, value)
        elif field in INT_RANGES:
            out[field] = normalise_int(field, value)
        elif field == "tags":
            out[field] = normalise_tags(value)
        elif field == "custom":
            out[field] = normalise_custom(value)
        elif field == "place_id":
            out[field] = places.normalise_id(value)
        else:
            out[field] = _text(field, value)
    return out
