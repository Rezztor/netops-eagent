"""Turning a catalogue entry into the commands an engineer will approve.

There is one computed source and one reviewed one, and which was used is
recorded on the change:

* `check_mode: supported` or `partial` -- run the entry against the devices with
  `--check --diff` and read the module's own `commands` from the structured
  events. Computed against the device as it is now.
* `check_mode: unsupported` -- the entry carries `reviewed_cli`, literal text a
  person wrote and reviewed. It cannot account for the device's state, and the
  record says so.

The offline third option the Ansible documentation implies -- `state: rendered`
-- does not exist for this estate. Verified against the built image on
2026-09-09: `cisco.ios` resource modules refuse `connection: local` outright, and
over `network_cli` they open the session before the module runs;
`dellemc.enterprise_sonic` does not accept `rendered` at all.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from plugins._ea_ansible.helpers import catalogue, executor_client
from plugins._ea_ansible.helpers.change_model import FROM_CHECK_MODE, FROM_REVIEWED_CLI


class RenderFailed(Exception):
    """The commands could not be produced, so there is nothing to approve."""


def fill(text: str, variables: dict[str, Any], quote: Any = str) -> str:
    """Replace each `<name>` that is one of `variables` with its value.

    Anything else in angle brackets is left alone, so the card shows what a person
    wrote rather than a guess. `quote` is `re.escape` when the text is a regex.
    """
    return catalogue.PLACEHOLDER.sub(
        lambda m: quote(str(variables[m.group(1)])) if m.group(1) in variables else m.group(0), text
    )


def diff_lines(diffs: list[Any]) -> list[str]:
    """The `--diff` objects a check-mode run reported, as unified-diff lines for the card."""
    out: list[str] = []
    for item in diffs:
        if not isinstance(item, dict):
            out += str(item).splitlines()
        elif item.get("prepared"):
            out += str(item["prepared"]).splitlines()
        else:
            out += difflib.unified_diff(
                str(item.get("before") or "").splitlines(),
                str(item.get("after") or "").splitlines(),
                str(item.get("before_header") or "before"),
                str(item.get("after_header") or "after"),
                lineterm="",
            )
    return out


def render(
    agent: Any,
    entry: catalogue.Entry,
    variables: dict[str, Any],
    limit: str,
    commit: str,
) -> dict[str, Any]:
    """Return the commands, the diff, and how they were arrived at."""
    if entry.check_mode == "unsupported":
        if not entry.reviewed_cli:
            raise RenderFailed(
                f"'{entry.name}' cannot report its commands and carries no reviewed CLI."
            )
        return {
            "commands": [fill(line, variables) for line in entry.reviewed_cli],
            "diff": [],
            "render_method": FROM_REVIEWED_CLI,
        }

    result = executor_client.run(
        agent,
        playbook_name=entry.name,
        commit=commit,
        variables=variables,
        limit=limit,
        check_mode=True,
    )
    if not result.succeeded:
        # The playbook's own last failure message, when it left one: a plain sentence beats an exit code.
        said = re.findall(r'"msg": "((?:[^"\\]|\\.)*)"', result.output or "")
        raise RenderFailed(
            f"The check-mode run of '{entry.name}' failed (exit code {result.exit_code}), "
            "so there are no commands to approve." + (f" It said: {said[-1][:300]}" if said else "")
        )

    commands = result.commands()
    if not commands:
        # A check-mode run that reports nothing means the device already matches
        # the intent. That is a real answer, and it is not an approval request.
        return {"commands": [], "diff": result.diffs(), "render_method": FROM_CHECK_MODE}

    return {
        "commands": commands,
        "diff": result.diffs(),
        "render_method": FROM_CHECK_MODE,
    }


def rollback_plan(entries: dict[str, Any], name: str, variables: dict[str, Any]) -> tuple[Any, dict[str, Any], list[str]]:
    """The rollback entry, the variables it takes from the change, and its filled commands.

    Raises what `catalogue.resolve` and `validate_variables` raise: a change whose
    variables do not fit its rollback cannot be offered, because it could not be undone.
    """
    entry = catalogue.resolve(name, entries)
    mine = entry.validate_variables({k: v for k, v in variables.items() if k in entry.variables})
    return entry, mine, [fill(line, mine) for line in entry.reviewed_cli]
