"""What of a job's output the model is shown.

A read used to hand over the last 6,000 characters of Ansible's raw output. Ansible prints a read's commands
in alphabetical order after a screen of banners and "skipping" lines for the platforms the device is not, so
a three-command read lost its first command whole and the engineer was asked to approve the same read again
(found on c3725-lab, 2026-10-04).

Here the banners go first, and what is left is cut per command: every command keeps its head and its tail and
says how many lines were dropped between them. No command is dropped.
"""

from __future__ import annotations

import json
import re

LIMIT = 12000  # characters of output the model sees for one job
_NOISE = re.compile(r"^(PLAY \[|TASK \[|skipping: \[|ok: \[[^\]]*\]$|changed: \[[^\]]*\]$)")
_BLOCK = re.compile(r"^(ok|changed): \[([^\]]+)\] => (\{\n.*?\n\})$", re.S | re.M)


def excerpt(output: str, limit: int = LIMIT) -> str:
    """`output` without Ansible's banners, at most about `limit` characters, with every command present."""
    blocks = [_commands(m.group(2), m.group(3)) for m in _BLOCK.finditer(output)]
    shown = [b for b in blocks if b]
    if shown:
        commands = sum(len(b[1]) for b in shown)
        budget = max(400, limit // max(1, commands))
        parts = []
        for host, outputs in shown:
            for command, lines in outputs.items():
                parts.append(f"# {host}: {command}\n{_fit(lines, budget)}")
        recap = output[output.rfind("PLAY RECAP"):] if "PLAY RECAP" in output else ""
        return "\n\n".join(parts + ([_strip(recap)] if recap else []))
    return _fit(_strip(output).splitlines(), limit)


def _commands(host: str, block: str) -> tuple[str, dict[str, list[str]]] | None:
    """One debug block as {command: lines}, or None when it is not a dict of command output."""
    try:
        msg = json.loads(block).get("msg")
    except (ValueError, AttributeError):
        return None
    if not isinstance(msg, dict) or not msg:
        return None
    return host, {str(k): [str(x) for x in v] if isinstance(v, list) else str(v).splitlines() for k, v in msg.items()}


def _strip(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip() and not _NOISE.match(line)]
    return "\n".join(lines)


def _fit(lines: list[str], budget: int) -> str:
    """`lines` whole when they fit; otherwise the head and the tail, with the count dropped between them."""
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    head, tail, used = [], [], 0
    front, back = 0, len(lines) - 1
    while front <= back:
        for take_front in (True, False):
            if front > back:
                break
            line = lines[front] if take_front else lines[back]
            if used + len(line) + 1 > budget and (head or tail):
                dropped = back - front + 1
                note = f"[... {dropped} lines not shown: ask again with a narrower command to see them ...]"
                return "\n".join(head + [note] + tail[::-1])
            used += len(line) + 1
            if take_front:
                head.append(line[:budget])
                front += 1
            else:
                tail.append(line[:budget])
                back -= 1
    return "\n".join(head + tail[::-1])
