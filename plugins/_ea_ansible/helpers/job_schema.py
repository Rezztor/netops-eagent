"""The executor's job contract, owned by both sides.

The application builds a job from these fields and the executor validates against
the same names. It is a module rather than a docstring because E3 tests it: there
must be no field through which playbook content, a raw device command, a device
address or a credential can arrive.

See ADR 0030 -- "what the executor will not accept" is this frozenset's
complement, and the reason it is written down.
"""

from __future__ import annotations

# What decides which playbook runs, against what, and how. A name and a commit
# resolve to a reviewed playbook on the executor; variables are validated against
# that entry's schema there.
EXECUTION_FIELDS: frozenset[str] = frozenset(
    {
        "playbook_name",
        "commit",
        "variables",
        "limit",
        "check_mode",
    }
)

# Labels the executor stores against the job and never acts on. They exist so the
# console can say who asked and which change a run belongs to; they are kept
# separate from the execution fields because that separation is testable, and
# `test_record_only_fields_never_reach_ansible` tests it.
#
# `actor` is asserted by the caller and cannot be verified here. It is worth
# recording only because the token that carried it is trusted -- which is also
# why it is a label in a history view and never an input to an authorisation
# decision.
RECORD_ONLY_FIELDS: frozenset[str] = frozenset({"actor", "change_id"})

# Everything a job may carry.
ALLOWED_JOB_FIELDS: frozenset[str] = EXECUTION_FIELDS | RECORD_ONLY_FIELDS

# Names that must never become job fields, whatever a future refactor wants.
# Each one is a way for content, a command or a credential to reach the executor,
# which is precisely what the catalogue exists to prevent.
FORBIDDEN_JOB_FIELDS: frozenset[str] = frozenset(
    {
        "playbook",
        "playbook_content",
        "content",
        "yaml",
        "tasks",
        "module",
        "command",
        "commands",
        "raw",
        "shell",
        "script",
        "host",
        "hosts",
        "device",
        "device_ip",
        "address",
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "credentials",
        "private_key",
        "ssh_key",
        "vault_password",
    }
)


#: A job is finished when it reaches one of these. `queued` and `running` are
#: not terminal, and that is exactly why this lives in the shared contract: a
#: poller that treats "not running" as "done" reports a queued job as a finished
#: one, and the two sides have to agree on the word.
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed"})


def validate_job_fields(fields: object) -> None:
    """Raise if a job carries anything outside the contract.

    Used by the client before submitting and by the executor before running, so
    the two cannot drift apart into a gap somebody can post through.
    """
    if not isinstance(fields, dict):
        raise ValueError("A job must be an object.")

    names = set(fields)
    forbidden = names & FORBIDDEN_JOB_FIELDS
    if forbidden:
        raise ValueError(
            "Job carries fields the executor refuses: "
            + ", ".join(sorted(forbidden))
        )
    unknown = names - ALLOWED_JOB_FIELDS
    if unknown:
        raise ValueError("Unknown job fields: " + ", ".join(sorted(unknown)))
