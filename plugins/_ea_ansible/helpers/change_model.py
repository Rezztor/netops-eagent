"""One change, from draft to closed, and the hash an engineer approves.

The record is the unit of change control. It carries the commands that would run,
who approved them, and a hash over the whole plan -- so "was this approved?" and
"is this still what was approved?" are two different questions with two different
answers, which is the point of condition 3.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# The happy path, then the ways out. Every terminal state says something a person
# would want to know; `superseded` in particular is not a failure, it is the gate
# noticing the world moved.
DRAFT = "draft"
RENDERED = "rendered"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
APPLYING = "applying"
APPLIED = "applied"
CLOSED = "closed"
REJECTED = "rejected"
CANCELLED = "cancelled"
SUPERSEDED = "superseded"
FAILED = "failed"
ROLLED_BACK = "rolled_back"
# Applied, but the entry's own verify did not find the intended state. The card
# that follows offers the rollback.
VERIFY_FAILED = "verify_failed"
# A read card nobody answered in time. Terminal, and not a failure.
EXPIRED = "expired"

OPEN_STATES = (DRAFT, RENDERED, AWAITING_APPROVAL, APPROVED, APPLYING)
TERMINAL_STATES = (CLOSED, REJECTED, CANCELLED, SUPERSEDED, FAILED, ROLLED_BACK, EXPIRED)

#: How the commands in a record were arrived at. Recorded because the two are not
#: equally trustworthy and an approver is entitled to know which they are reading.
FROM_CHECK_MODE = "check_mode"
FROM_REVIEWED_CLI = "reviewed_cli"


@dataclass
class ChangeRecord:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    requested_by: str = ""

    status: str = DRAFT
    #: "change", "read", "rollback" (a change's undo, approved on its own card) or
    #: "plan" (up to eight ordered steps approved once, ADR 0053). All four are the
    #: same record with the same gate; only the card, the lifetime and the tool that
    #: runs it differ.
    kind: str = "change"

    playbook_name: str = ""
    commit: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    limit: str = ""
    summary: str = ""

    # What the device would receive, and where that text came from.
    commands: list[str] = field(default_factory=list)
    diff: list[Any] = field(default_factory=list)
    render_method: str = ""
    rendered_at: float | None = None

    # The way back, approved at the same moment as the way in.
    rollback_name: str = ""
    rollback_commands: list[str] = field(default_factory=list)

    approved_by: str = ""
    approved_at: float | None = None
    approved_hash: str = ""

    applied_at: float | None = None
    exit_code: int | None = None
    error: str = ""

    # The approval lapses at `expires_at` (reads and changes); `preflight` and
    # `devices_resolved` are the evidence the card showed (never part of the hash);
    # `job_id` and `output_excerpt` are what the run left behind (a change's
    # excerpt is its verify's).
    expires_at: float | None = None
    preflight: dict[str, Any] = field(default_factory=dict)
    devices_resolved: list[dict[str, Any]] = field(default_factory=list)
    job_id: str = ""
    output_excerpt: str = ""
    #: Reads only, never part of the hash. The chat the request was made in, and
    #: how it was approved: "" = a person pressed Approve on the card, otherwise
    #: "standing:once_per_chat" / "standing:log_only" (ADR 0048).
    context_id: str = ""
    approval_basis: str = ""

    # Changes (ADR 0050). `tier` and `confirm` are decided by code at draft:
    # "change-high" carries the text an approver must type. `verified` is True or
    # False after the entry's verify ran, None when it declares none. `rollback_of`
    # is set on a rollback record, `rollback_id` on the change it undoes.
    tier: str = ""
    confirm: str = ""
    verified: bool | None = None
    rollback_of: str = ""
    rollback_id: str = ""

    #: A plan only (`helpers/plans.py`): its steps, in order. Hashed whole, so approval
    #: binds to every step's variables, devices and commands, not to a summary of them.
    steps: list[dict[str, Any]] = field(default_factory=list)

    # Azure changes (`plugins/_ea_azure_change`). `kind` is then azure_change, azure_action,
    # azure_suggestion or azure_read; a device record leaves all three at their defaults.
    #: The plan digest (SHA-256 over the executor's canonical plan). Part of the hash when set, so
    #: every hash an engineer already approved still matches.
    plan_digest: str = ""
    #: The CR or SR number typed at approval. Never part of the hash: it says who allowed it, not what runs.
    reference: str = ""
    #: Never part of the hash: [{version: "issued" | "applied" | "runbook", format: "pdf" | "docx",
    #: sha256, created_at, downloaded_at}].
    documents: list[dict[str, Any]] = field(default_factory=list)
    #: The server's signature over what a gate trusts (`change_store._seal`). Never part of the hash, never sent
    #: to a browser or a model: it is written to the file and checked when the file is read.
    seal: str = ""

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and (time.time() if now is None else now) >= self.expires_at

    @property
    def current_hash(self) -> str:
        """A hash of the plan as it stands now.

        Deliberately excludes anything about *who* or *when*: the question it
        answers is "would this run the same commands against the same devices",
        and an approval timestamp has no bearing on that.
        """
        parts: dict[str, Any] = dict(
            playbook_name=self.playbook_name,
            commit=self.commit,
            variables=self.variables,
            limit=self.limit,
            commands=self.commands,
            rollback_commands=self.rollback_commands,
        )
        if self.kind != "change":
            # Left out for changes so every hash an engineer already approved
            # still matches; a read or a rollback can never hash like a change.
            parts["kind"] = self.kind
        if self.kind == "plan":
            parts["steps"] = self.steps
        if self.plan_digest:
            parts["plan_digest"] = self.plan_digest
        return plan_hash(**parts)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        del data["seal"]
        data["current_hash"] = self.current_hash
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangeRecord":
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: SLF001 - dataclass API
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


def plan_hash(**parts: Any) -> str:
    """Stable across processes and key order, which a Python hash is not."""
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
