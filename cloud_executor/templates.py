"""The template catalogue: load `templates.yaml`, validate typed values, render `terraform.tfvars.json`.

A template is reviewed OpenTofu code (`templates/<name>/main.tf`) plus an entry here that types every variable.
There is no free text: a model supplies a name and values, and a value that fails its type is refused before any
code runs. The loader runs the static gate over every template at load time, so a template that could not pass
`gate.check_code` is not in the catalogue. The entry keys are described in `templates/README.md`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from cloud_executor import adopt, gate

ROOT = Path(__file__).resolve().parent
ENTRY_KEYS = {"name", "summary", "risk", "types", "variables", "workspace_variables", "may_change",
              "restarts_existing", "backout", "identity", "parents"}
#: Names a template may not take: they mark the other kinds of change in a record's `playbook_name`.
RESERVED_NAMES = {"set", "free", "adopt", "drift_accept", "suggestion"}
VARIABLE_TYPES = {"str", "enum", "int", "bool", "region", "cidr", "list", "tags"}
WORKSPACE_VARIABLES = {"subscription_id", "resource_group"}
_NAME = re.compile(r"[a-z][a-z0-9_]{1,40}")
_REGION = re.compile(r"[a-z0-9]{3,30}")
_TAG_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_TAG_VALUE = re.compile(r"[^<>%&\\?/\x00-\x1f\x7f]{0,256}")  # no control character: a line break would forge a line on the card


class CatalogueError(Exception):
    """A template or the catalogue is malformed. The message names the entry."""


class VariableRejected(Exception):
    """A value failed its type. The message is shown to the agent and the engineer."""


def _check_spec(where: str, spec: Any, top: bool = True) -> None:
    if not isinstance(spec, dict) or spec.get("type") not in VARIABLE_TYPES:
        raise CatalogueError(f"{where}: the type must be one of {sorted(VARIABLE_TYPES)}.")
    if spec["type"] == "str":
        try:
            re.compile(spec["pattern"])
        except (KeyError, re.error, TypeError) as error:
            raise CatalogueError(f"{where}: a str needs a pattern that compiles ({error}).") from error
    if spec["type"] == "enum" and not isinstance(spec.get("values"), list):
        raise CatalogueError(f"{where}: an enum needs values.")
    if spec["type"] == "list":
        if not isinstance(spec.get("max"), int) or not isinstance(spec.get("item"), dict) or not top:
            raise CatalogueError(f"{where}: a list needs max and an item of named fields, and cannot nest.")
        for field, sub in spec["item"].items():
            _check_spec(f"{where}.{field}", sub, top=False)


class Entry:
    """One catalogue entry, with the code that goes with it."""

    def __init__(self, raw: dict[str, Any], root: Path) -> None:
        unknown = set(raw) - ENTRY_KEYS
        name = str(raw.get("name") or "")
        if unknown or not _NAME.fullmatch(name):
            raise CatalogueError(f"{name or '(unnamed)'}: unknown keys {sorted(unknown)} or a bad name.")
        if name in RESERVED_NAMES:
            raise CatalogueError(f"{name}: a template cannot take the reserved name {name}.")
        if raw.get("risk") not in ("low", "high"):
            raise CatalogueError(f"{name}: risk must be low or high.")
        self.name: str = name
        self.summary: str = str(raw.get("summary") or "")
        self.risk: str = raw["risk"]
        self.types: list[str] = list(raw.get("types") or [])
        self.variables: dict[str, dict[str, Any]] = dict(raw.get("variables") or {})
        self.workspace_variables: list[str] = list(raw.get("workspace_variables") or [])
        self.may_change: list[str] = list(raw.get("may_change") or [])
        self.restarts_existing: bool = bool(raw.get("restarts_existing"))
        self.backout: str = str(raw.get("backout") or "")
        #: Variables that name what this change makes. Two changes with the same values share a state; any other
        #: difference is a new state. Absent: every variable counts, which means no change is ever an update.
        self.identity: list[str] | None = raw.get("identity")
        self.directory = root / "templates" / name
        if not self.types or not self.backout.strip() or not (self.directory / "main.tf").is_file():
            raise CatalogueError(f"{name}: needs types, a back-out text and templates/{name}/main.tf.")
        if set(self.workspace_variables) - WORKSPACE_VARIABLES or set(self.workspace_variables) & set(self.variables):
            raise CatalogueError(f"{name}: workspace_variables must be {sorted(WORKSPACE_VARIABLES)} and not also typed.")
        for variable, spec in self.variables.items():
            _check_spec(f"{name}.{variable}", spec)
        #: What an earlier step of the same set may make (see `parent_ids`). A step names its parents and refers to
        #: them by name or built ID in `main.tf`, never through a `data` block, so its plan is the same whether the
        #: parent exists or is still to be made.
        self.parents: list[dict[str, str]] = [self._parent(raw_parent) for raw_parent in raw.get("parents") or []]
        refusals = gate.check_code(self.code_files())
        if refusals:
            raise CatalogueError(f"{name}: the static gate refuses its code: {refusals[0]}")

    def _parent(self, raw: Any) -> dict[str, str]:
        where = f"{self.name}.parents"
        if not isinstance(raw, dict) or not {"type", "name"} <= set(raw) <= {"type", "name", "resource_group", "unless"}:
            raise CatalogueError(f"{where}: each parent is {{type, name}} with optional resource_group and unless.")
        arm = adopt.ARM_TYPES.get(str(raw["type"]).lower())
        if not arm or arm[0].count("/") != 1:
            raise CatalogueError(f"{where}: {raw['type']} is not a top-level type of adopt.ARM_TYPES.")
        for key in ("name", "resource_group", "unless"):
            if key in raw and (raw[key] not in self.variables or self.variables[raw[key]]["type"] != ("bool" if key == "unless" else "str")):
                raise CatalogueError(f"{where}: {key} must name a {'bool' if key == 'unless' else 'str'} variable of the entry.")
        return {**{k: str(v) for k, v in raw.items()}, "type": arm[0].lower()}

    def parent_ids(self, clean: dict[str, Any], subscription: str, resource_group: str) -> list[tuple[str, str]]:
        """`(lower-case Azure ID, lower-case type)` of what this change needs to exist, or to be made by an earlier step.
        A parent whose optional variable is left out, or whose `unless` is true, is skipped."""
        out = []
        for parent in self.parents:
            name = clean.get(parent["name"])
            group = clean.get(parent["resource_group"]) if "resource_group" in parent else resource_group
            if name is None or group is None or clean.get(parent.get("unless", "")) is True:
                continue
            arm = adopt.ARM_TYPES[parent["type"]][0]
            out.append((f"/subscriptions/{subscription}/resourceGroups/{group}/providers/{arm}/{name}".lower(), parent["type"]))
        return out

    def code_files(self) -> dict[str, str]:
        """Every file of the template (path relative to it), so a stray `.tfvars` or `.tf.json` fails the gate."""
        return {path.relative_to(self.directory).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(self.directory.rglob("*")) if path.is_file() and path.name != "README.md"}

    # ------------------------------------------------------------------------------------------ the values

    def validate(self, values: Any, regions: list[str] | None = None) -> dict[str, Any]:
        """The typed values, or `VariableRejected`. `regions`, when given, are the guard rails' regions."""
        if not isinstance(values, dict):
            raise VariableRejected("variables must be an object.")
        for key in values:
            if key not in self.variables:
                raise VariableRejected(f"'{key}' is not a variable of {self.name}.")
        clean: dict[str, Any] = {}
        for key, spec in self.variables.items():
            if key not in values:
                if not spec.get("optional"):
                    raise VariableRejected(f"'{key}' is required.")
                continue
            clean[key] = _typed(key, spec, values[key], regions)
        return clean

    def tfvars(self, clean: dict[str, Any], subscription: str, resource_group: str) -> dict[str, Any]:
        """What goes into `terraform.tfvars.json`: validated values plus the workspace's own."""
        workspace = {"subscription_id": subscription, "resource_group": resource_group}
        return {**clean, **{k: workspace[k] for k in self.workspace_variables}}

    def target_ids(self, clean: dict[str, Any]) -> list[str]:
        """Azure IDs named by string values (for `restarts_existing`)."""
        return [v for v in clean.values() if isinstance(v, str) and v.lower().startswith("/subscriptions/")]

    def state_key(self, clean: dict[str, Any], subscription: str, resource_group: str) -> str:
        """The state this change lives in: `<subscription>/<resource group>/<template>/<hash of what it makes>`."""
        names = self.identity if self.identity is not None else sorted(clean)
        body = json.dumps({k: clean.get(k) for k in names}, sort_keys=True, separators=(",", ":"))
        return f"{subscription.lower()}/{resource_group}/{self.name}/{hashlib.sha256(body.encode()).hexdigest()[:16]}"


def _typed(where: str, spec: dict[str, Any], value: Any, regions: list[str] | None) -> Any:
    kind = spec["type"]
    bad = VariableRejected
    if kind == "str":
        if not isinstance(value, str) or not re.fullmatch(spec["pattern"], value):
            raise bad(f"'{where}' does not match the pattern for it.")
    elif kind == "enum":
        if value not in spec["values"]:
            raise bad(f"'{where}' must be one of {spec['values']}.")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not spec.get("min", value) <= value <= spec.get("max", value):
            raise bad(f"'{where}' must be a whole number from {spec.get('min', 'any')} to {spec.get('max', 'any')}.")
    elif kind == "bool":
        if not isinstance(value, bool):
            raise bad(f"'{where}' must be true or false.")
    elif kind == "region":
        if not isinstance(value, str) or not _REGION.fullmatch(value):
            raise bad(f"'{where}' must be an Azure region name such as uaenorth.")
        if regions is not None and value.lower() not in {re.sub(r"[\s_-]", "", r).lower() for r in regions}:
            raise bad(f"The region {value} is not one the guard rails allow.")
    elif kind == "cidr":
        try:
            net = ipaddress.ip_network(value if isinstance(value, str) else None, strict=True)  # a number is a /32 to ipaddress
        except (ValueError, TypeError) as error:
            raise bad(f"'{where}' must be an IPv4 network such as 10.20.0.0/16 with no host bits set.") from error
        if net.version != 4 or not spec.get("min_prefix", 0) <= net.prefixlen <= spec.get("max_prefix", 32):
            raise bad(f"'{where}' must be an IPv4 network with a prefix from /{spec.get('min_prefix', 0)} to /{spec.get('max_prefix', 32)}.")
    elif kind == "tags":
        if not isinstance(value, dict) or len(value) > 50 or not all(
                isinstance(k, str) and isinstance(v, str) and _TAG_KEY.fullmatch(k) and _TAG_VALUE.fullmatch(v)
                for k, v in value.items()):
            raise bad(f"'{where}' must be up to 50 tags with plain names and values (no < > % & \\ ? /).")
        missing = [k for k in spec.get("required", []) if not value.get(k)]
        if missing:
            raise bad(f"'{where}' needs the tags {missing}.")
    elif kind == "list":
        if not isinstance(value, list) or not spec.get("min", 0) <= len(value) <= spec["max"]:
            raise bad(f"'{where}' must be a list of {spec.get('min', 0)} to {spec['max']} items.")
        fields: dict[str, Any] = spec["item"]
        out = []
        for position, item in enumerate(value):
            if not isinstance(item, dict) or set(item) - set(fields):
                raise bad(f"'{where}' item {position + 1} must be an object with only {sorted(fields)}.")
            row = {}
            for field, sub in fields.items():
                if field in item:
                    row[field] = _typed(f"{where}[{position + 1}].{field}", sub, item[field], regions)
                elif not sub.get("optional"):
                    raise bad(f"'{where}' item {position + 1} needs '{field}'.")
            out.append(row)
        return out
    return value


def load(root: Path | str | None = None) -> dict[str, Entry]:
    """The catalogue, by name. A malformed entry or a directory with no entry is an error, never skipped."""
    base = Path(root) if root else Path(os.getenv("REZZTOR_CLOUD_TEMPLATES_ROOT") or ROOT)
    try:
        raw = yaml.safe_load((base / "templates.yaml").read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as error:
        raise CatalogueError(f"templates.yaml cannot be read: {error}") from error
    if not isinstance(raw, list) or not all(isinstance(e, dict) for e in raw):
        raise CatalogueError("templates.yaml must be a list of entries.")
    entries = {}
    for item in raw:
        entry = Entry(item, base)
        if entry.name in entries:
            raise CatalogueError(f"{entry.name}: listed twice.")
        entries[entry.name] = entry
    on_disk = {p.name for p in (base / "templates").iterdir() if p.is_dir()} if (base / "templates").is_dir() else set()
    if on_disk - set(entries):
        raise CatalogueError(f"templates/ has directories with no entry: {sorted(on_disk - set(entries))}.")
    return entries
