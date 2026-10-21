"""The playbook catalogue, and the schema every variable is checked against.

Loaded identically by the application (to draft a change) and by the executor
(to refuse a job that does not name a real entry). One implementation rather
than two, because two would drift and the gap would be the way in.

Nothing here reaches the network or runs Ansible. It reads YAML, validates
values, and answers questions about entries.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE_FILE = os.path.join(PLUGIN_DIR, "catalogue.yaml")

KINDS = ("read", "change", "rollback")
CHECK_MODES = ("supported", "partial", "unsupported")
VARIABLE_TYPES = ("string", "integer", "boolean", "choice")
RISKS = ("low", "high")
#: `<name>` in a reviewed CLI line or a verify's `expect` stands for that variable's value.
PLACEHOLDER = re.compile(r"<([a-z][a-z0-9_]*)>")

#: A variable name is an identifier. Anything else is a way to smuggle
#: structure into a place that expects a name.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: An entry name is a path-shaped identifier: `reads/device_facts`.
ENTRY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*$")


class CatalogueError(Exception):
    """The catalogue itself is wrong. A startup problem, not a request problem."""


class UnknownEntry(Exception):
    """The requested entry is not in the catalogue."""


class VariableRejected(Exception):
    """A supplied variable failed its declared constraint."""


@dataclass(frozen=True)
class Variable:
    name: str
    type: str
    required: bool = False
    pattern: str | None = None
    choices: tuple[Any, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def coerce(self, value: Any) -> Any:
        """Return the value in its declared type, or raise VariableRejected.

        Deliberately strict. A value that merely looks convertible is refused
        rather than guessed at, because the result of this function is handed to
        Ansible, which templates what it is given.
        """
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            raise VariableRejected(f"'{self.name}' must be true or false.")

        if self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise VariableRejected(f"'{self.name}' must be a whole number.")
            if self.minimum is not None and value < self.minimum:
                raise VariableRejected(f"'{self.name}' must be at least {self.minimum}.")
            if self.maximum is not None and value > self.maximum:
                raise VariableRejected(f"'{self.name}' must be at most {self.maximum}.")
            return value

        if not isinstance(value, str):
            raise VariableRejected(f"'{self.name}' must be text.")

        if self.type == "choice":
            if value not in self.choices:
                raise VariableRejected(
                    f"'{self.name}' must be one of: " + ", ".join(map(str, self.choices))
                )
            return value

        # string
        if self.pattern and not re.fullmatch(self.pattern, value):
            raise VariableRejected(
                f"'{self.name}' contains characters this entry does not accept."
            )
        return value


@dataclass(frozen=True)
class Entry:
    name: str
    playbook: str
    kind: str
    summary: str
    check_mode: str
    platforms: tuple[str, ...] = ()
    rollback: str | None = None
    #: Reviewed, literal CLI for entries whose modules cannot report what
    #: they would do. Written by the human who reviewed the playbook, not
    #: computed -- so it cannot account for the device's current state.
    reviewed_cli: tuple[str, ...] = ()
    #: platform -> the literal commands that reach the device for this read,
    #: shown on the approval card. Required for every read and every platform.
    read_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    variables: dict[str, Variable] = field(default_factory=dict)
    #: Changes only. `low` lets a one-to-three-device draft skip the typed
    #: confirmation; anything else is `high` (ADR 0050). Code decides, never the agent.
    risk: str = "high"
    #: `{commands: {platform: [show ...]}, expect: regex, absent: bool}` or None.
    #: Run through `reads/show` after an apply to prove the intended state.
    verify: dict[str, Any] | None = None

    @property
    def changes_state(self) -> bool:
        return self.kind == "change"

    def validate_variables(self, supplied: Any) -> dict[str, Any]:
        """Check supplied variables against this entry's schema.

        Unknown names are refused rather than dropped: silently ignoring an
        argument teaches the model that the argument works.
        """
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            raise VariableRejected("Variables must be an object.")

        unknown = set(supplied) - set(self.variables)
        if unknown:
            raise VariableRejected(
                f"'{self.name}' does not take: " + ", ".join(sorted(unknown))
            )

        result: dict[str, Any] = {}
        for name, variable in self.variables.items():
            if name not in supplied:
                if variable.required:
                    raise VariableRejected(f"'{name}' is required by '{self.name}'.")
                continue
            result[name] = variable.coerce(supplied[name])
        return result


def _variable_from(name: str, raw: Any) -> Variable:
    if not NAME_PATTERN.fullmatch(name):
        raise CatalogueError(f"Variable name is not an identifier: {name!r}")
    if not isinstance(raw, dict):
        raise CatalogueError(f"Variable {name!r} must be an object.")

    kind = str(raw.get("type") or "").strip()
    if kind not in VARIABLE_TYPES:
        raise CatalogueError(
            f"Variable {name!r} has type {kind!r}; expected one of {VARIABLE_TYPES}."
        )

    choices = tuple(raw.get("choices") or ())
    pattern = raw.get("pattern")

    # The constraint is the point of the schema, so an unconstrained variable is
    # a catalogue error rather than a permissive default.
    if kind == "string" and not pattern:
        raise CatalogueError(
            f"Variable {name!r} is a string with no pattern. Every string "
            "variable must declare what it accepts."
        )
    if kind == "choice" and not choices:
        raise CatalogueError(f"Variable {name!r} is a choice with no choices.")
    if pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CatalogueError(f"Variable {name!r} has an invalid pattern: {exc}") from exc

    return Variable(
        name=name,
        type=kind,
        required=bool(raw.get("required", False)),
        pattern=pattern,
        choices=choices,
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
    )


def _entry_from(raw: Any) -> Entry:
    if not isinstance(raw, dict):
        raise CatalogueError("Every catalogue entry must be an object.")

    name = str(raw.get("name") or "").strip()
    if not ENTRY_PATTERN.fullmatch(name):
        raise CatalogueError(f"Entry name is not a catalogue path: {name!r}")

    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        raise CatalogueError(f"Entry {name!r} has kind {kind!r}; expected one of {KINDS}.")

    check_mode = str(raw.get("check_mode") or "").strip()
    if check_mode not in CHECK_MODES:
        raise CatalogueError(
            f"Entry {name!r} declares check_mode {check_mode!r}; expected one of {CHECK_MODES}."
        )

    playbook = str(raw.get("playbook") or "").strip()
    if not playbook or playbook.startswith("/") or ".." in playbook:
        raise CatalogueError(f"Entry {name!r} has an unusable playbook path: {playbook!r}")

    variables = {
        key: _variable_from(key, value)
        for key, value in (raw.get("variables") or {}).items()
    }

    # Where the commands an engineer approves actually come from.
    #
    # Verified against the built image on 2026-09-09, and it is not what the
    # Ansible documentation implies: `state: rendered` is described as working
    # offline, but cisco.ios resource modules refuse `connection: local`
    # outright, and dellemc.enterprise_sonic does not accept `rendered` at all.
    # So there is no general offline renderer. Either check mode reports the
    # commands -- computed against the device, which is the good case -- or the
    # entry has to carry CLI a human reviewed, and say so.
    reviewed_cli = tuple(str(line) for line in (raw.get("reviewed_cli") or ()))
    if check_mode == "unsupported" and not reviewed_cli:
        raise CatalogueError(
            f"Entry {name!r} declares check_mode 'unsupported', so nothing can compute "
            "the commands it would push. It must carry reviewed_cli: the literal CLI, "
            "reviewed by a person."
        )
    if check_mode == "supported" and reviewed_cli:
        raise CatalogueError(
            f"Entry {name!r} supports check mode, so its commands come from the device. "
            "Static reviewed_cli would go stale beside them."
        )

    platforms = tuple(raw.get("platforms") or ())
    raw_commands = raw.get("read_commands") or {}
    if not isinstance(raw_commands, dict):
        raise CatalogueError(f"Entry {name!r}: read_commands must map platform to a list.")
    read_commands = {
        str(platform): tuple(str(line).strip() for line in (lines or ()))
        for platform, lines in raw_commands.items()
    }
    if kind == "read":
        for platform in platforms:
            # A card needs something to show, so even a module-based read lists
            # what it runs.
            if not any(read_commands.get(platform, ())):
                raise CatalogueError(
                    f"Entry {name!r} is a read for {platform!r} but lists no read_commands for it."
                )
        if set(read_commands) - set(platforms):
            raise CatalogueError(f"Entry {name!r} lists read_commands for a platform it does not support.")

    risk = str(raw.get("risk") or "high").strip()
    if risk not in RISKS:
        raise CatalogueError(f"Entry {name!r} has risk {risk!r}; expected one of {RISKS}.")
    if risk == "low" and kind != "change":
        raise CatalogueError(f"Entry {name!r} declares a risk but is not a change.")
    verify = raw.get("verify")
    if verify is not None:
        # The commands are checked against `reads/show` in `load`, once it is known.
        if not isinstance(verify, dict) or not isinstance(verify.get("commands"), dict) or not verify.get("expect"):
            raise CatalogueError(f"Entry {name!r}: verify needs `commands` (platform to list) and `expect`.")
        if set(verify["commands"]) - set(platforms):
            raise CatalogueError(f"Entry {name!r}: verify lists commands for a platform it does not support.")
        for placeholder in PLACEHOLDER.findall(str(verify["expect"])):
            if placeholder not in variables:
                raise CatalogueError(f"Entry {name!r}: verify expects <{placeholder}>, which is not a variable.")
        try:
            re.compile(PLACEHOLDER.sub("0", str(verify["expect"])))
        except re.error as exc:
            raise CatalogueError(f"Entry {name!r}: verify `expect` is not a regex: {exc}") from exc

    return Entry(
        name=name,
        playbook=playbook,
        kind=kind,
        summary=str(raw.get("summary") or "").strip(),
        check_mode=check_mode,
        platforms=platforms,
        rollback=(str(raw["rollback"]).strip() if raw.get("rollback") else None),
        reviewed_cli=reviewed_cli,
        read_commands=read_commands,
        variables=variables,
        risk=risk,
        verify=verify,
    )


def load(path: str = CATALOGUE_FILE) -> dict[str, Entry]:
    """Read and fully validate the catalogue.

    Raises rather than returning a partial catalogue: a half-loaded catalogue is
    a catalogue with a hole in it, and the hole is where an unreviewed entry
    would sit.
    """
    with open(path, "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}

    if not isinstance(document, dict):
        raise CatalogueError("The catalogue must be a mapping.")

    entries: dict[str, Entry] = {}
    for raw in document.get("entries") or []:
        entry = _entry_from(raw)
        if entry.name in entries:
            raise CatalogueError(f"Duplicate catalogue entry: {entry.name!r}")
        entries[entry.name] = entry

    # Checked after the whole file is read, so a rollback may be declared above
    # or below the change that names it.
    for entry in entries.values():
        if entry.changes_state and not entry.rollback:
            raise CatalogueError(
                f"Entry {entry.name!r} changes state but names no rollback entry."
            )
        if entry.rollback and entry.rollback not in entries:
            raise CatalogueError(
                f"Entry {entry.name!r} names rollback {entry.rollback!r}, which is not in the catalogue."
            )

    # A verify is a read: its commands must pass the same pattern as any free-form
    # `show` (ADR 0046), so a catalogue edit cannot slip a write in through it.
    show = entries.get("reads/show")
    for entry in entries.values():
        for platform, lines in ((entry.verify or {}).get("commands") or {}).items():
            if show is None or not 1 <= len(lines or []) <= 5:
                raise CatalogueError(f"Entry {entry.name!r}: verify ({platform}) needs 1 to 5 show commands and reads/show.")
            try:
                show.validate_variables({f"command_{i}": c for i, c in enumerate(lines, 1)})
            except VariableRejected as error:
                raise CatalogueError(f"Entry {entry.name!r}: verify ({platform}): {error}") from error

    return entries


def resolve(name: Any, entries: dict[str, Entry] | None = None) -> Entry:
    """Find one entry by name, or refuse.

    A miss is a catalogue miss, never an improvisation: the agent is told the
    entry does not exist rather than being offered something close to it.
    """
    catalogue = entries if entries is not None else load()
    key = str(name or "").strip()
    entry = catalogue.get(key)
    if entry is None:
        raise UnknownEntry(
            f"'{key}' is not in the playbook catalogue. Only reviewed playbooks can run."
        )
    return entry


def playbook_path(entry: Entry, root: str = PLUGIN_DIR) -> str:
    """Absolute path to an entry's playbook, confined to the catalogue root."""
    resolved = os.path.realpath(os.path.join(root, entry.playbook))
    if not resolved.startswith(os.path.realpath(root) + os.sep):
        raise CatalogueError(f"Entry {entry.name!r} resolves outside the catalogue.")
    return resolved
