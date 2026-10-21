"""What `ea-ansible` calls. Kept in Python so it shares the catalogue's own code.

A shell script that reimplemented variable validation would be a second answer to
what a variable may contain, and the second answer is always the one that is
wrong. This reads the same catalogue the executor reads and validates with the
same code the API validates with.

The honest limit, stated here as well as in the wrapper: this records what it
runs, and cannot stop what it does not run. A shell in this container reaches
`ansible-playbook` directly. Shell access here is full authority over the estate.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import uuid
from typing import Any

from executor import redaction, store
from plugins._ea_ansible.helpers import catalogue


def _fail(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    raise SystemExit(1)


def describe(argv: list[str]) -> None:
    try:
        entries = catalogue.load()
    except catalogue.CatalogueError as error:
        _fail(f"The catalogue will not load: {error}")

    action = argv[0] if argv else "list"
    if action == "list":
        for name in sorted(entries):
            entry = entries[name]
            print(f"{name:48s} {entry.kind:9s} {entry.check_mode:12s} {entry.summary.strip()[:60]}")
        return

    if len(argv) < 2:
        _fail("usage: ea-ansible show <entry>")
    entry = entries.get(argv[1])
    if entry is None:
        _fail(f"No catalogue entry named {argv[1]!r}.")

    print(f"name        {entry.name}")
    print(f"kind        {entry.kind}")
    print(f"platforms   {', '.join(entry.platforms)}")
    print(f"check_mode  {entry.check_mode}")
    if entry.rollback:
        print(f"rollback    {entry.rollback}")
    if entry.reviewed_cli:
        print("reviewed_cli")
        for line in entry.reviewed_cli:
            print(f"            {line}")
    print("variables")
    for name, variable in entry.variables.items():
        constraint = getattr(variable, "pattern", "") or getattr(variable, "choices", "") or ""
        print(f"            {name:20s} {variable.type:9s} {'required' if variable.required else 'optional':9s} {constraint}")


def run(argv: list[str]) -> None:
    if not argv:
        _fail("usage: ea-ansible run <entry> --limit <pattern> [--check] [-e key=value ...]")

    name = argv[0]
    rest = argv[1:]
    limit = ""
    check = False
    supplied: dict[str, Any] = {}

    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--limit" and index + 1 < len(rest):
            limit = rest[index + 1]
            index += 2
        elif token == "--check":
            check = True
            index += 1
        elif token in ("-e", "--extra-var") and index + 1 < len(rest):
            key, _, value = rest[index + 1].partition("=")
            supplied[key] = value
            index += 2
        else:
            _fail(f"Unrecognised argument: {token}")

    try:
        entries = catalogue.load()
    except catalogue.CatalogueError as error:
        _fail(f"The catalogue will not load: {error}")

    entry = entries.get(name)
    if entry is None:
        _fail(f"No catalogue entry named {name!r}. Try: ea-ansible list")

    try:
        variables = entry.validate_variables(supplied)
    except catalogue.VariableRejected as error:
        _fail(str(error))

    if not limit:
        # The API caps a job's device count; this refuses an unbounded pattern
        # outright, because a person at a shell who forgot --limit is about to
        # run against the whole inventory.
        _fail("--limit is required. Running against the whole inventory is not a default.")

    if entry.kind == "change" and not check:
        sys.stderr.write(
            "This is a change entry and --check was not given, so it will push "
            "configuration.\n"
        )

    job_id = uuid.uuid4().hex
    started = time.time()
    store.store().record(
        {
            "job_id": job_id,
            "source": "cli",
            "actor": os.getenv("USER") or os.getenv("LOGNAME") or "shell",
            "playbook": entry.name,
            "catalogue_commit": os.getenv("REZZTOR_ANSIBLE_CATALOGUE_COMMIT") or "unpinned",
            "variables": redaction.redact_structure(variables, keep_operational=True),
            "limit_pattern": limit,
            "check_mode": check,
            "status": "running",
            "exit_code": None,
            "started_at": started,
            "finished_at": None,
            "events": [],
            "output": "",
            "error": "",
            "change_id": "",
        }
    )

    command = [
        "ansible-playbook",
        catalogue.playbook_path(entry),
        "--limit",
        limit,
    ]
    for key, value in variables.items():
        command += ["-e", f"{key}={value}"]
    if check:
        command += ["--check", "--diff"]
    inventory = os.getenv("REZZTOR_ANSIBLE_INVENTORY")
    if inventory:
        command += ["-i", inventory]

    sys.stderr.write("+ " + " ".join(shlex.quote(part) for part in command) + "\n")
    completed = subprocess.run(command, check=False)

    store.store().update(
        job_id,
        status="succeeded" if completed.returncode == 0 else "failed",
        exit_code=completed.returncode,
        finished_at=time.time(),
        # The output went to the operator's terminal, not through here. Saying so
        # is better than an empty field that reads like a job that printed
        # nothing.
        output="(ran from a shell; output went to the terminal)",
    )
    raise SystemExit(completed.returncode)


def backup(argv: list[str]) -> None:
    """`ea-ansible backup list|export`. Export is the one place raw configuration text leaves the store."""
    from executor import backups, credentials  # noqa: PLC0415 - only this command needs them

    try:
        if argv[:1] == ["list"]:
            for host, rows in backups.store().list(argv[1] if len(argv) > 1 else None).items():
                for row in rows:
                    print(f"{host:24s} {row['id']} {row['taken_at']} {row['size']:>8d} bytes")
        elif argv[:1] == ["export"] and len(argv) == 3:
            sys.stdout.write(backups.store().raw(argv[1], argv[2]))
        else:
            _fail("usage: ea-ansible backup list [host] | backup export <host> <id>")
    except (backups.BackupError, credentials.CredentialError) as error:
        _fail(str(error))
