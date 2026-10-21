"""Named show-command bundles, and the one place a read's command lines are built.

`bundles.yaml` maps a name to up to five show commands per platform. Every command
is checked against the `reads/show` pattern (ADR 0046) as the file loads, so a
bundle can never carry anything a free-form read could not.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from plugins._ea_ansible.helpers import catalogue

BUNDLES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bundles.yaml")
MAX_COMMANDS = 5


class BundleError(Exception):
    """bundles.yaml is unusable."""


def load(path: str = BUNDLES_FILE, show: Any = None) -> dict[str, dict[str, Any]]:
    show = show or catalogue.load()["reads/show"]
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    for name, bundle in raw.items():
        commands = (bundle or {}).get("commands") or {}
        if not (bundle or {}).get("summary") or not commands:
            raise BundleError(f"bundle '{name}' needs a summary and commands")
        for platform, lines in commands.items():
            if not 1 <= len(lines or []) <= MAX_COMMANDS:
                raise BundleError(f"bundle '{name}' ({platform}) needs 1 to {MAX_COMMANDS} commands")
            try:
                show.validate_variables({f"command_{i}": c for i, c in enumerate(lines, 1)})
            except catalogue.VariableRejected as error:
                raise BundleError(f"bundle '{name}' ({platform}): {error}") from error
    return raw


def commands_for(name: str, platforms: set[str], show: Any = None) -> tuple[list[str], str]:
    """The bundle's commands for the one platform asked about, or the refusal."""
    bundles = load(show=show)
    if name not in bundles:
        return [], f"There is no bundle '{name}'. Bundles: {', '.join(sorted(bundles))}."
    if len(platforms) != 1:
        return [], (
            f"The devices span platforms ({', '.join(sorted(platforms))}) and a bundle's commands are "
            "per platform: split by platform, one request each."
        )
    (platform,) = platforms
    lines = bundles[name]["commands"].get(platform)
    if not lines:
        return [], f"Bundle '{name}' has no commands for platform `{platform}` (it has: {', '.join(sorted(bundles[name]['commands']))})."
    return list(lines), ""


def read_lines(entry: Any, devices: list[dict[str, Any]], variables: dict[str, Any]) -> tuple[list[str], str]:
    """What reaches each platform, as the card shows it, or the refusal.

    `<name>` in an entry's `read_commands` stands for a variable: the card shows its
    value, or nothing when unset. Shared by a read request and a card edit, so the
    two cannot disagree about the text an approval binds to.
    """
    out: list[str] = []
    for platform in sorted({d["platform"] for d in devices}):
        lines = entry.read_commands.get(platform)
        if not lines:
            return [], (
                f"`{entry.name}` has no reviewed commands for platform `{platform}` "
                f"(it supports: {', '.join(sorted(entry.read_commands)) or 'none'}). "
                "Nothing was requested; pick another capability."
            )
        who = ", ".join(d["name"] for d in devices if d["platform"] == platform)
        lines = [variables.get(line[1:-1], "") if line[:1] == "<" and line[-1:] == ">" else line for line in lines]
        out += [f"# {who} ({platform})", *[line for line in lines if line]]
    return out, ""
