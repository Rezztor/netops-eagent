"""Read the estate, with an engineer's yes in front of every read.

A read changes nothing on a device, but it does open a session with a credential
the engineer never handed the agent, so each one is a request an engineer
approves in the chat. `request` resolves the targets against active Inventory,
pre-checks them, records a `kind="read"` change record and puts a decision card
in front of the engineer; it never connects to anything. `run` executes a
request that was approved, unchanged and still in date. Every capability is a
catalogue entry of kind `read`, run in check mode, so there is no argument
through which this tool can write. `reads/show` takes the engineer's own show
commands; the catalogue pattern on its variables is what keeps those read-only
(ADR 0046), and the card shows the exact text that will be sent; the engineer
may edit that text on the card before approving. A fixed read or a bundle can be
approved by a standing setting instead of a card (`read_approval`, ADR 0048);
free-form commands always get a card.
"""

from __future__ import annotations

import re
import time
from typing import Any

from helpers import tool_permissions, user_scope
from helpers.print_style import PrintStyle
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import bundles, catalogue, change_store, conditions, executor_client
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import excerpt as excerpts
from plugins._ea_ansible.helpers import failures, preflight
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
from plugins._ea_inventory.helpers import redact, store

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

#: How long an approved read stays runnable. Short on purpose: the engineer
#: approved a moment, not a standing permission.
EXPIRY_SECONDS = 15 * 60
MAX_DEVICES = 10  # the executor's preflight cap
RESERVED = {"capability", "devices", "action", "change_id", "commands", "bundle"}
#: Said when a free-form command is refused, so the agent can fix it instead of guessing.
SHOW_RULE = (
    "Each command must be the word `show` followed by 1 to 12 plain words (letters, digits "
    "and _ . / : , -), 160 characters at most, lower-case `show`. No pipes, redirects, quotes "
    "or `;`, no option that starts with `-`, no `//`, and no tech-support (no word starting with `tec`). At most 5 commands."
)


class NetRead(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "request").strip().lower()

        if not plugin_config.is_configured(self.agent):
            return _say(
                "Network automation is not configured on this instance. An "
                "administrator must set the Ansible executor address and token."
            )
        if action == "request":
            return self._request(args)
        if action == "run":
            return self._run(args)
        return _say(f"Unknown action '{action}'. Use request or run.")

    # --- request: resolve, pre-check, ask ------------------------------------

    def _request(self, args: dict[str, Any]) -> Response:
        bundle = str(args.get("bundle") or "").strip()
        capability = "reads/show" if bundle else str(args.get("capability") or "").strip()
        names = [t for t in re.split(r"[,\s]+", str(args.get("devices") or "")) if t]
        if not names:
            return _say("A read has to name the devices it targets.")
        if len(names) > MAX_DEVICES:
            return _say(f"Ask for at most {MAX_DEVICES} devices per read; split the request.")

        try:
            entries = catalogue.load()
            entry = catalogue.resolve(capability, entries)
        except catalogue.UnknownEntry as error:
            return _say(f"{error}\n\nReads that exist, with the commands each sends:\n{_readable()}")
        except catalogue.CatalogueError as error:
            return _say(f"The playbook catalogue is unusable: {error}")
        if entry.kind != "read":
            return _say(
                f"'{entry.name}' changes state, so it cannot run through net_read. "
                "Use net_change to draft it for approval."
            )
        devices = None
        if bundle:
            # `command_1`..`command_5` are the same thing as `commands`: one left beside the bundle's lines
            # would run on the bundle's standing approval.
            if args.get("commands") or any(re.fullmatch(r"command_\d+", str(k)) for k in args):
                return _say("Pass a bundle or your own commands, not both.")
            devices, refusal = _resolve(names)
            if refusal:
                return _say(refusal)
            lines, refusal = bundles.commands_for(bundle, {d["platform"] for d in devices}, entry)
            if refusal:
                return _say(refusal)
            args = {**args, "commands": lines}
        supplied = {k: v for k, v in args.items() if k not in RESERVED}
        free_form = "command_1" in getattr(entry, "variables", {})
        if free_form and args.get("commands"):  # `commands` is the list form of command_1..n
            listed = args["commands"]
            listed = listed.splitlines() if isinstance(listed, str) else list(listed)
            supplied.update({f"command_{i}": c.strip() if isinstance(c, str) else c
                             for i, c in enumerate([c for c in listed if c], 1)})
        try:
            variables = entry.validate_variables(supplied)
        except catalogue.VariableRejected as error:
            return _say(f"{error}\n\n{SHOW_RULE}" if free_form else str(error))

        if devices is None:
            devices, refusal = _resolve(names)
            if refusal:
                return _say(refusal)

        commands, refusal = bundles.read_lines(entry, devices, variables)
        if refusal:
            return _say(refusal)

        try:
            checked = preflight.run_preflight(self.agent, devices, entry)
            commit = str(executor_client.health(self.agent).get("catalogue_commit") or "")
        except executor_client.ExecutorError as error:
            return _say(f"The pre-check could not run, so no request was made: {error}")
        if not checked["ok"]:
            failed = "\n".join(f"- {c['label']}: {c['detail']}" for c in checked["checks"] if not c["ok"])
            missing = _show_missing_credentials(self.agent, checked)
            if missing:
                return Response(
                    message=(
                        f"`{entry.name}` on `{', '.join(names)}` cannot be read yet:\n{failed}\n\n"
                        f"A secure form for {missing} is now on screen. Stop and wait for the "
                        "user to answer \"Credential <name> stored.\", then request the read "
                        "again. Never ask for the value in chat."
                    ),
                    break_loop=True,
                )
            return _say(
                f"`{entry.name}` on `{', '.join(names)}` cannot be read right now, so no "
                f"approval was requested:\n{failed}\n\nTell the user what is wrong; do not "
                "ask them for credentials unless a check says one is missing."
            )

        record = ChangeRecord(
            kind="read",
            requested_by=user_scope.get_current_user_id() or "",
            status=AWAITING_APPROVAL,
            playbook_name=entry.name,
            commit=commit,
            variables=variables,
            limit=",".join(d["name"] for d in devices),
            summary=entry.summary,
            commands=commands,
            render_method=FROM_REVIEWED_CLI,
            expires_at=time.time() + EXPIRY_SECONDS,
            preflight={"checks": checked["checks"]},
            devices_resolved=checked["devices"],
            context_id=str(getattr(getattr(self.agent, "context", None), "id", "") or ""),
        )
        # Free-form commands always ask; a fixed read or a bundle may be covered by a standing setting.
        basis = "" if free_form and not bundle else self._standing_basis(record)
        if basis:
            change_store.approve_standing(record, basis)
            if audit is not None:
                audit.emit(
                    "network.read.auto_approved",
                    change_id=record.id,
                    requested_by=record.requested_by,
                    basis=basis,
                    limit=record.limit,
                    approved_hash=record.approved_hash,
                )
            return _say(
                f"Read request `{record.id}` (`{entry.name}` on `{record.limit}`) is covered by the "
                f"standing read setting ({basis}), so no card was needed. Call net_read with "
                f"action=run and change_id={record.id} now."
            )
        change_store.save(record)

        # Unlike a change, the card is the only way this request can be approved,
        # so failing to show it cancels the record instead of leaving it dangling.
        try:
            self._show_card(record, entry, checked)
        except Exception as error:  # noqa: BLE001 - see comment above
            record.status = CANCELLED
            change_store.save(record)
            return _say(f"The approval card could not be shown, so the request was cancelled: {error}")

        return _say(
            f"Read request `{record.id}` (`{entry.name}` on `{record.limit}`) is waiting "
            "for the engineer's approval on the card in the chat. Nothing has run. Stop "
            "here and wait: do not call any other tool and do not ask for credentials. "
            f"If they answer Approve, call net_read with action=run and change_id={record.id}; "
            "if they answer Reject, stop."
        )

    def _standing_basis(self, record: ChangeRecord) -> str:
        """`standing:<mode>` when the `read_approval` setting covers this fixed read, else "".

        The policy is `conditions.standing_basis`; this only gathers what it needs.
        """
        mode = plugin_config.get_config(self.agent)["read_approval"]
        if mode == "every_time":
            return ""
        role = tool_permissions.role_for_context(getattr(self.agent, "context", None))
        return conditions.standing_basis(record, mode, role, change_store.list_records())

    def _show_card(self, record: ChangeRecord, entry: Any, checked: dict[str, Any]) -> None:
        from helpers.parallel_tools import user_facing_context

        kvps = read_card(record, editable="command_1" in getattr(entry, "variables", {}))
        user_facing_context(self.agent).log.log(
            type="info", heading=kvps["question"], content="\n".join(record.commands), kvps=kvps
        )

    # --- run: only what was approved -----------------------------------------

    def _run(self, args: dict[str, Any]) -> Response:
        actor = user_scope.get_current_user_id() or ""
        try:
            record = change_store.load(str(args.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return _say(str(error))

        if record.kind != "read":
            return _say(f"`{record.id}` is a change, not a read. Changes run through net_apply.")
        if record.requested_by != actor:
            return _say(f"Read request `{record.id}` was not made by the current user, so it will not run.")
        if record.status == AWAITING_APPROVAL and record.expired():
            record.status = EXPIRED
            change_store.save(record)
        if record.status != APPROVED:
            if record.status == AWAITING_APPROVAL:
                return _say(
                    f"Read request `{record.id}` is not approved yet: nobody has pressed Approve on "
                    "its card, so nothing ran. Typing 'approve' in the chat does not approve it. Tell "
                    "the user to press Approve on the card, then stop and wait."
                )
            return _say(
                f"Read request `{record.id}` is '{record.status}', not approved, so nothing "
                "ran. If it expired or was rejected, ask the user whether to request it again."
            )
        refusal = conditions.first_failure(record, actor)  # hash unchanged, approver, not expired
        if refusal:
            return _say(refusal)

        if not change_store.take(record):  # one statement in the approval register: an approval runs once
            return _say(f"Read request `{record.id}` has no approval left to use (it already ran, or is running), so nothing ran.")
        try:
            result = executor_client.run(
                self.agent,
                playbook_name=record.playbook_name,
                commit=record.commit,
                variables=record.variables,
                limit=record.limit,
                check_mode=True,
            )
        except executor_client.ExecutorError as error:
            self._finish(record, FAILED, error=str(error), failure="unknown")
            return _say(f"The read did not run: {error}")

        excerpt = redact.redact_text(excerpts.excerpt(result.output))[0]
        record.job_id, record.output_excerpt, record.exit_code = result.job_id, excerpt, result.exit_code
        if result.succeeded:
            self._finish(record, CLOSED)
            return _say(f"`{record.playbook_name}` on `{record.limit}`\n\n```\n{excerpt}\n```")
        found = failures.classify(result.output)
        self._finish(record, FAILED, error=f"{found['code']}: {found['cause']}", failure=found["code"])
        return _say(
            f"`{record.playbook_name}` on `{record.limit}` failed: **{found['code']}** — "
            f"{found['cause']}\n\n```\n{excerpt[-1500:]}\n```"
        )

    def _finish(self, record: ChangeRecord, status: str, error: str = "", failure: str = "") -> None:
        record.status, record.error, record.applied_at = status, error, time.time()
        change_store.save(record)
        if audit is not None:
            audit.emit(
                "network.read.run",
                change_id=record.id,
                requested_by=record.requested_by,
                approved_by=record.approved_by,
                approved_hash=record.approved_hash,
                job_id=record.job_id,
                playbook=record.playbook_name,
                limit=record.limit,
                exit_code=record.exit_code,
                failure=failure,
            )


def read_card(record: ChangeRecord, editable: bool = False) -> dict[str, str]:
    """The decision card for a read record, built from the record alone.

    Shared by the request and by a card edit, so the refreshed card carries the
    new commands, hash and expiry. `editable` offers the free-form edit (ADR 0048).
    """
    from plugins._ea_interaction.helpers import cards

    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    rows = [("Capability", record.playbook_name)]
    for d in record.devices_resolved:
        facts = (d.get("mgmt_ip"), d.get("network"), d.get("site"), d.get("platform"), d.get("credential"))
        rows.append((str(d.get("name")), " · ".join(str(f) for f in facts if f)))
    return cards.decision_card(
        f"Read `{record.playbook_name}` on {record.limit}?",
        endpoint="plugins/_ea_ansible/change_approve",
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        consequence=(
            "Read-only: nothing on the device changes. Approving lets the agent run "
            f"these commands once, within {EXPIRY_SECONDS // 60} minutes."
        ),
        details=rows,
        commands=record.commands,
        checks=[{"label": c["label"], "ok": c["ok"], "detail": c.get("detail", "")} for c in record.preflight.get("checks", [])],
        expires_at=record.expires_at,
        tier="read",
        **({"edit_endpoint": "plugins/_ea_ansible/read_edit", "edit_payload": payload} if editable else {}),
    )


def _show_missing_credentials(agent: Any, checked: dict[str, Any]) -> str:
    """Show the secure form for each device whose pre-check lacks a credential.

    Returns the quoted names shown, or "" when none applied or the form could not
    be shown (the plain reason is then all the agent gets, as before).
    """
    from plugins._ea_interaction.helpers import cards

    wanted = {
        d["credential"]
        for c in checked["checks"]
        if not c["ok"] and c["label"].endswith(": credential present")
        for d in checked["devices"]
        if d.get("credential") and c["label"].startswith(f"{d['name']}:")
    }
    shown = []
    for name in sorted(wanted):
        try:
            cards.show_secret_form(agent, name)
            shown.append(f"`{name}`")
        except Exception as error:  # noqa: BLE001 - a card that fails must not break the read reply
            PrintStyle.error(f"net_read: credential form for '{name}' not shown: {error}")
    return ", ".join(shown)


def _readable() -> str:
    """One line per read: its summary and, per platform, what reaches the device."""
    lines = []
    for name, e in sorted(catalogue.load().items()):
        if e.kind == "read":
            sent = "; ".join(f"{p}: {', '.join(c)}" for p, c in sorted(e.read_commands.items()))
            lines.append(f"- {name}: {e.summary} ({sent})")
    return "\n".join(lines)


def _say(message: str) -> Response:
    return Response(message=message, break_loop=False)


def _resolve(names: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Active Inventory devices for each name or management IP, or the refusal."""
    found: list[dict[str, Any]] = []
    for token in names:
        hits = store.search_devices(name=token)[0] or [
            d for d in store.search_devices(q=token, limit=50)[0] if d["mgmt_ip"] == token
        ]
        if not hits:
            return [], (
                f"`{token}` is not in Inventory, and net_read only reads devices Inventory "
                "lists as active. Nothing was requested. Offer to stage it through an "
                "Inventory proposal (inventory_stage) so an engineer can review and activate it."
            )
        device = hits[0]
        if device["status"] != "active":
            return [], (
                f"`{device['name']}` is in Inventory but its status is '{device['status']}'; "
                "only active devices can be read. Ask an engineer to activate it."
            )
        if device not in found:
            found.append(device)
    return found, ""
