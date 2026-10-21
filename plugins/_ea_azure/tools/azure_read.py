"""Ask Azure a question about one resource, with an engineer's yes in front of it.

Built like `net_read` (ADR 0048): `request` checks the named read and the resource against the stored inventory,
records a `kind="azure_read"` change record and puts a decision card in front of the engineer; it never contacts
Azure. `run` sends the request that was approved, unchanged and still in date, through `helpers/live.py`. The model
names a read from `reads.yaml` and a stored resource and fills typed arguments; there is no argument through which it
can give an address, a method or a body, and a read that is not in the table cannot be asked. A GET read or a probe
(a POST that only answers a question, the Rezztor Diagnostics role) can be approved by the standing `read_approval`
setting instead of a card; nothing else exists here.

`run_refusal` is the one check of an approved record; the tool and the gate (`_95_azure_read_gate.py`) both call it.
Nothing of Azure's answer is kept: the record holds the request and who approved it, never the body (ADR 0067).

The same tool asks Log Analytics (ADR 0069): a named log read from `reads.yaml` (`kind: kql`) or a free-form `kql`, each
in a `workspace` chosen from the settings, through `helpers/logs.py` and its guard. A named log read can be covered by
`read_approval` like any other; a free-form query always asks, and its card can be edited (`azure_read_edit`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from helpers import rbac, tool_permissions, user_scope
from helpers.tool import Response, Tool

from plugins._audit.helpers import audit
from plugins._ea_ansible.helpers import change_store, conditions
from plugins._ea_ansible.helpers.change_model import (
    APPROVED,
    AWAITING_APPROVAL,
    CANCELLED,
    CLOSED,
    EXPIRED,
    FAILED,
    FROM_REVIEWED_CLI,
    ChangeRecord,
)
from plugins._ea_azure.helpers import config, live, logs, store

KIND = "azure_read"
#: How long an approved read stays runnable: the engineer approved a moment, not a standing permission.
EXPIRY_SECONDS = 15 * 60
RESERVED = {"action", "read", "resource", "change_id"}
LOG_ARGS = {"kql", "workspace"}


class AzureRead(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "request").strip().lower()
        if action == "request":
            return self._request(args)
        if action == "run":
            return await self._run(args)
        return _say(f"Unknown action '{action}'. Use request or run.")

    # --- request: resolve, check, ask ----------------------------------------

    def _request(self, args: dict[str, Any]) -> Response:
        try:
            entries, queries = live.load(), logs.load()
        except live.ReadsInvalid as error:
            return _say(f"The list of named Azure reads is unusable, so nothing was requested: {error}")
        name = str(args.get("read") or "").strip()
        kql = str(args.get("kql") or "").strip()
        if kql or name in queries:
            return self._request_logs(args, name, kql)
        if not config.is_configured():
            return _say("Azure is not set up on this instance. An administrator must save the tenant, the application and its certificate.")
        entry = entries.get(name)
        if entry is None:
            return _say(f"{'There is no read named `' + name + '`. ' if name else ''}Reads that exist (name, the type of resource it "
                        f"takes, the role it needs):\n{live.catalogue()}\n{logs.catalogue()}\n"
                        "Or pass `kql` and `workspace` for a free-form Log Analytics query.")
        resource = str(args.get("resource") or "").strip()
        if not resource:
            return _say(f"{name} needs `resource`: the name or full ID of a {entry.resource_type} in the Azure inventory.")
        try:
            target = live.resolve(entry, resource)
            typed = live.check_args(entry, {k: v for k, v in args.items() if k not in RESERVED})
        except live.Refused as error:
            return _say(str(error))

        pulled = (store.as_of().get("last_pull") or {}).get("started_at") or "an earlier pull"
        checks = [
            {"label": "Resource is in the stored inventory", "ok": True, "detail": f"{target.name}, {target.type}, last pulled {pulled}"},
            {"label": "Answer cut to an allow-list; nothing is stored", "ok": True, "detail": ""},
        ]
        if entry.scope == "watcher":
            checks.append({"label": f"A Network Watcher exists in {target.region}", "ok": True, "detail": target.base.rsplit("/", 1)[-1]})
        if entry.method == "POST":
            checks.append({"label": "A probe: Azure answers, nothing changes", "ok": True,
                           "detail": f"needs the Rezztor Diagnostics role ({entry.requires})"})

        record = ChangeRecord(
            kind=KIND,
            requested_by=user_scope.get_current_user_id() or "",
            status=AWAITING_APPROVAL,
            playbook_name=entry.name,
            commit=live.commit(),
            variables=typed,
            limit=target.native_id,
            summary=entry.summary,
            commands=live.describe(entry, target, typed),
            render_method=FROM_REVIEWED_CLI,
            expires_at=time.time() + EXPIRY_SECONDS,
            preflight={"checks": checks},
            devices_resolved=[{"name": target.name, "type": target.type, "resource_group": target.group,
                               "subscription": target.subscription, "region": target.region}],
            context_id=str(getattr(getattr(self.agent, "context", None), "id", "") or ""),
        )
        return self._record(record, entry, target.name, editable=False)

    def _request_logs(self, args: dict[str, Any], name: str, kql: str) -> Response:
        try:
            made = logs.prepare(name, kql, args.get("workspace"), {k: v for k, v in args.items() if k not in RESERVED | LOG_ARGS})
        except live.Refused as error:
            return _say(str(error))
        entry, ws = made["entry"], made["limit"]
        checks = [{"label": "Workspace is one the settings name", "ok": True, "detail": ws},
                  {"label": "Query passed the guard", "ok": True,
                   "detail": "one statement, only the allowed tables, no other workspace or external data, identity columns left out, at most 200 rows"},
                  {"label": "Answer cut and masked; nothing is stored", "ok": True, "detail": ""}]
        record = ChangeRecord(
            kind=KIND, requested_by=user_scope.get_current_user_id() or "", status=AWAITING_APPROVAL, playbook_name=entry.name,
            commit=live.commit(), variables=made["variables"], limit=ws, summary=entry.summary, commands=made["commands"],
            render_method=FROM_REVIEWED_CLI, expires_at=time.time() + EXPIRY_SECONDS, preflight={"checks": checks},
            devices_resolved=[{"name": ws, "type": "log analytics workspace"}],
            context_id=str(getattr(getattr(self.agent, "context", None), "id", "") or ""),
        )
        return self._record(record, entry, ws, editable=entry is logs.FREE_ENTRY)

    def _record(self, record: ChangeRecord, entry: Any, shown: str, editable: bool) -> Response:
        """Save the request and show its card, or record it as covered by the standing setting."""
        basis = self._standing_basis(record)
        if basis:
            change_store.approve_standing(record, basis)
            _audit("azure.read.requested", record)
            _audit("azure.read.auto_approved", record, basis=basis, approved_hash=record.approved_hash)
            return _say(f"Read request `{record.id}` (`{entry.name}` on `{shown}`) is covered by the standing read setting "
                        f"({basis}), so no card was needed. Call azure_read with action=run and change_id={record.id} now.")
        change_store.save(record)
        _audit("azure.read.requested", record)
        # The card is the only way this request can be approved: failing to show it cancels the record.
        try:
            self._show_card(record, entry, editable) if editable else self._show_card(record, entry)
        except Exception as error:  # noqa: BLE001
            record.status = CANCELLED
            change_store.save(record)
            return _say(f"The approval card could not be shown, so the request was cancelled: {error}")
        return _say(f"Read request `{record.id}` (`{entry.name}` on `{shown}`) is waiting for the engineer's approval on the "
                    "card in the chat. Nothing has been sent to Azure. Stop here and wait: do not call any other tool. If they "
                    f"answer Approve, call azure_read with action=run and change_id={record.id}; if they answer Reject, stop.")

    def _standing_basis(self, record: ChangeRecord) -> str:
        """`standing:<mode>` when the `read_approval` setting covers this read, else "".

        The setting moves when a card is shown, not who may approve: only a requester who holds `network.approve` qualifies
        (ADR 0039, 0048). `once_per_chat` needs a card-approved read of this same resource by this user in this chat.
        """
        mode = live.read_approval()
        if record.playbook_name == logs.FREE:
            return ""  # free-form text always asks (D10)
        role = tool_permissions.role_for_context(getattr(self.agent, "context", None))
        if mode == "every_time" or not record.requested_by or not rbac.has_permission(role, rbac.PERM_NETWORK_APPROVE):
            return ""
        if mode == "log_only":
            return "standing:log_only"
        seen = any(r.kind == KIND and r.context_id == record.context_id and r.approved_by == record.requested_by
                   and r.approval_basis == "" and r.limit == record.limit and r.playbook_name != logs.FREE
                   for r in change_store.list_records())
        return "standing:once_per_chat" if record.context_id and seen else ""

    def _show_card(self, record: ChangeRecord, entry: Any, editable: bool = False) -> None:
        from helpers.parallel_tools import user_facing_context

        kvps = read_card(record, entry, editable)
        user_facing_context(self.agent).log.log(type="info", heading=kvps["question"], content="\n".join(record.commands), kvps=kvps)

    # --- run: only what was approved -----------------------------------------

    async def _run(self, args: dict[str, Any]) -> Response:
        actor = user_scope.get_current_user_id() or ""
        try:
            record = change_store.load(str(args.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return _say(str(error))
        if record.status == AWAITING_APPROVAL and record.expired():
            record.status = EXPIRED
            change_store.save(record)
        refusal = run_refusal(record, actor)
        if refusal:
            return _say(refusal)

        if not change_store.take(record):  # one statement in the approval register: an approval runs once
            return _say(f"Read request `{record.id}` has no approval left to use (it already ran, or is running), so nothing ran.")
        is_log = logs.known(record.playbook_name)
        try:
            if is_log:
                v = record.variables
                done = await asyncio.to_thread(logs.query, v.get("workspace", ""), "\n".join(record.commands), v.get("timespan", ""), record.context_id)
            else:
                done = await asyncio.to_thread(live.read, record.playbook_name, record.limit, record.variables)
        except live.Refused as error:
            self._finish(record, FAILED, error=str(error))
            return _say(f"The read did not run: {error}")
        except live.ReadUnavailable as error:
            self._finish(record, FAILED, error=str(error), status=error.status)
            return _say(f"The read did not run: {error}")
        self._finish(record, CLOSED, status=done["status"], rows=done["rows"])
        if is_log:
            _audit("azure.logs.query", record, workspace=record.limit, timespan=record.variables.get("timespan"),
                   sha256=hashlib.sha256("\n".join(record.commands).encode()).hexdigest(), rows=done["rows"])
        entry = logs.entry_for(record.playbook_name) or live.load()[record.playbook_name]
        more = ("\nMore existed than is shown (a list is cut at 200 rows, or Azure had another page that was not read)."
                if done["truncated"] else "")
        note = f"\n{entry.cost_note}" if entry.cost_note else ""
        return _say(f"`{entry.name}` on `{record.limit}`: Azure's live answer at {done['at']} (UTC), not stored. It is the state at that "
                    f"moment, not a standing fact.{note}{more}\n\n{json.dumps(done['result'], indent=1)}")

    def _finish(self, record: ChangeRecord, outcome: str, error: str = "", **audit_fields: Any) -> None:
        record.status, record.error, record.applied_at = outcome, error, time.time()
        record.exit_code = audit_fields.get("status")
        change_store.save(record)
        _audit("azure.read.run", record, outcome=outcome, error=error, **audit_fields)


def run_refusal(record: ChangeRecord, actor: str) -> str | None:
    """Why this record may not be sent now, or None. Fails closed: a check that raises is a refusal.

    The actor is the requester; the record is approved, unchanged, in date and made against the table as it is now.
    """
    try:
        if record.kind != KIND:
            return f"`{record.id}` is not an Azure read. Changes run through their own tools."
        if not actor or record.requested_by != actor:
            return f"Read request `{record.id}` was not made by the current user, so it will not run."
        if record.status != APPROVED:
            if record.status == AWAITING_APPROVAL:
                return (f"Read request `{record.id}` is not approved yet: nobody has pressed Approve on its card, so nothing was "
                        "sent. Typing 'approve' in the chat does not approve it. Tell the user to press Approve on the card, then stop and wait.")
            return (f"Read request `{record.id}` is '{record.status}', not approved, so nothing was sent. If it expired or was "
                    "rejected, ask the user whether to request it again.")
        refusal = conditions.first_failure(record, actor)  # approver named, hash unchanged, not expired
        if refusal:
            return refusal
        entry = live.load().get(record.playbook_name) or logs.entry_for(record.playbook_name)
        if entry is None or record.commit != live.commit():
            return "The list of named Azure reads changed since this was approved, so it will not run. Ask for a fresh request."
        return None
    except Exception as error:  # noqa: BLE001 - a broken check must never be an open gate
        return f"The read could not be checked, so it will not run: {error}"


def read_card(record: ChangeRecord, entry: Any, editable: bool = False) -> dict[str, str]:
    """The decision card for an Azure read, built from the record alone. `editable` offers the free-form query's edit."""
    from plugins._ea_interaction.helpers import cards

    target = (record.devices_resolved or [{}])[0]
    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    if entry.role == "logs":
        rows = [("Read", f"{entry.name}: {entry.summary}"), ("Workspace", record.limit),
                ("Time range", str(record.variables.get("timespan") or "")), ("Tables", ", ".join(entry.tables) or "the allowed tables"),
                ("Azure role", "Log Analytics Reader (the log application)")]
    else:
        rows = [
            ("Read", f"{entry.name}: {entry.summary}"),
            ("Resource", " · ".join(str(target.get(k)) for k in ("name", "type", "resource_group", "region") if target.get(k))),
            ("Subscription", str(target.get("subscription") or "")),
            ("Azure role", "Rezztor Diagnostics (a probe)" if entry.role == "diagnostics" else "Reader"),
        ]
    if entry.cost_note:
        rows.append(("Cost", entry.cost_note))
    return cards.decision_card(
        f"Read `{entry.name}` on {target.get('name') or record.limit}?",
        endpoint="plugins/_ea_azure/azure_read_approve",
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        consequence=("Nothing in Azure changes" + (": this is a probe, Azure answers a question" if entry.method == "POST" else "")
                     + f". Approving lets the agent send this one request, once, within {EXPIRY_SECONDS // 60} minutes."),
        details=rows,
        commands=record.commands,
        checks=[{"label": c["label"], "ok": c["ok"], "detail": c.get("detail", "")} for c in record.preflight.get("checks", [])],
        expires_at=record.expires_at,
        tier="read",
        **({"edit_endpoint": "plugins/_ea_azure/azure_read_edit", "edit_payload": payload} if editable else {}),
    )


def _audit(event: str, record: ChangeRecord, **extra: Any) -> None:
    """Who asked for which read on which resource. Never the answer, never a token."""
    audit.emit(event, change_id=record.id, read=record.playbook_name, resource=record.limit,
               requested_by=record.requested_by, approved_by=record.approved_by, **extra)


def _say(message: str) -> Response:
    return Response(message=message, break_loop=False)
