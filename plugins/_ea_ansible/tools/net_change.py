"""Draft a change and put the exact commands in front of an engineer.

This tool never touches a device's configuration. Its most important action,
`draft`, runs the catalogue entry in check mode so the module reports what it
*would* do, records those commands, and hashes them. What an engineer then
approves is that text -- not a description of it.

The agent cannot approve its own draft. Approval is a separate action: the
requesting engineer reads the commands on the approval card in the chat and approves.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import Any

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import catalogue, change_store, preflight, render
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import executor_client
from plugins._ea_ansible.tools.net_read import MAX_DEVICES, _resolve
from plugins._ea_ansible.helpers.change_model import (
    AWAITING_APPROVAL,
    CANCELLED,
    FROM_REVIEWED_CLI,
    ChangeRecord,
)

#: How long an approved change stays appliable (reads get 15 minutes).
EXPIRY_SECONDS = 30 * 60
#: A change entry declaring `risk: low` is `change-low` only up to this many devices.
LOW_RISK_MAX_DEVICES = 3


def _changes() -> str:
    """One line per change entry: what it does, where it applies and what it needs."""
    lines = []
    for name, e in sorted(catalogue.load().items()):
        if e.kind == "change":
            needs = ", ".join(f"{v}{'' if spec.required else ' (optional)'}" for v, spec in e.variables.items()) or "nothing"
            lines.append(f"- {name}: {e.summary} Platforms: {', '.join(e.platforms)}. Needs: {needs}.")
    return "\n".join(lines) or "- none"


class NetChange(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "draft").strip().lower()

        if not plugin_config.is_configured(self.agent):
            return Response(
                message=(
                    "Network automation is not configured on this instance. An "
                    "administrator must set the Ansible executor address and token."
                ),
                break_loop=False,
            )

        if action == "draft":
            return await self._draft(args)
        if action == "status":
            return self._status(args)
        if action == "list":
            return self._list()
        if action == "cancel":
            return self._cancel(args)
        return Response(
            message=f"Unknown action '{action}'. Use draft, status, list or cancel.",
            break_loop=False,
        )

    async def _draft(self, args: dict[str, Any]) -> Response:
        capability = str(args.get("capability") or "").strip()
        names = [t for t in re.split(r"[,\s]+", str(args.get("devices") or "")) if t]
        if not names:
            return Response(
                message="A change has to name the devices it targets.", break_loop=False
            )
        if len(names) > MAX_DEVICES:
            return Response(
                message=f"Ask for at most {MAX_DEVICES} devices per change; split the request.",
                break_loop=False,
            )

        try:
            entries = catalogue.load()
            entry = catalogue.resolve(capability, entries)
        except catalogue.UnknownEntry as error:
            # Without this the agent guesses a name, is refused, and tells the engineer the
            # change cannot be made. Found on the first live draft against the lab.
            return Response(message=f"{error}\n\nChanges that exist:\n{_changes()}", break_loop=False)
        except catalogue.CatalogueError as error:
            return Response(message=str(error), break_loop=False)

        if entry.kind != "change":
            return Response(
                message=(
                    f"'{entry.name}' is a read. Run it with net_read."
                    if entry.kind == "read"
                    else f"'{entry.name}' is a rollback; it is offered after a change, not drafted."
                ),
                break_loop=False,
            )

        reserved = {"action", "capability", "devices", "change_id"}
        try:
            variables = entry.validate_variables(
                {k: v for k, v in args.items() if k not in reserved}
            )
            _, _, rollback_commands = render.rollback_plan(entries, entry.rollback, variables)
        except (catalogue.VariableRejected, catalogue.UnknownEntry) as error:
            return Response(message=str(error), break_loop=False)

        # Targets are active Inventory devices only, as for a read: an unknown or
        # inactive name is refused, never passed to the executor to be found out.
        found, refusal = _resolve(names)
        if refusal:
            return Response(message=refusal, break_loop=False)
        devices = ",".join(d["name"] for d in found)

        try:
            # `preflight` asks "does this entry have commands for this platform"
            # through `read_commands`, which a change has none of; the entry's
            # platforms stand in for it.
            asks = SimpleNamespace(
                name=entry.name, platforms=entry.platforms,
                read_commands={p: (entry.name,) for p in entry.platforms},
            )
            checked = preflight.run_preflight(self.agent, found, asks)
            health = executor_client.health(self.agent)
            commit = str(health.get("catalogue_commit") or "")
            if not checked["ok"]:
                failed = "\n".join(f"- {c['label']}: {c['detail']}" for c in checked["checks"] if not c["ok"])
                return Response(
                    message=(
                        f"`{entry.name}` on `{devices}` cannot be changed right now, so "
                        f"nothing was drafted:\n{failed}\n\nTell the user what is wrong. If a "
                        "check says a credential is missing, use net_credential."
                    ),
                    break_loop=False,
                )
            plan = render.render(self.agent, entry, variables, devices, commit)
        except (render.RenderFailed, executor_client.ExecutorError) as error:
            return Response(message=str(error), break_loop=False)

        if not plan["commands"]:
            return Response(
                message=(
                    f"`{entry.name}` would change nothing on `{devices}` — the devices "
                    "already match the intended state. No change drafted."
                ),
                break_loop=False,
            )

        # Decided here, by code, from the entry and the number of devices: the
        # agent has no argument that reaches it (ADR 0050).
        count = len(found)
        low = entry.risk == "low" and count <= LOW_RISK_MAX_DEVICES
        record = ChangeRecord(
            requested_by=user_scope.get_current_user_id() or "",
            status=AWAITING_APPROVAL,
            playbook_name=entry.name,
            commit=commit,
            variables=variables,
            limit=devices,
            summary=entry.summary,
            commands=plan["commands"],
            diff=plan["diff"],
            render_method=plan["render_method"],
            rollback_name=entry.rollback or "",
            rollback_commands=rollback_commands,
            expires_at=time.time() + EXPIRY_SECONDS,
            preflight={"checks": checked["checks"]},
            devices_resolved=checked["devices"],
            tier="change-low" if low else "change-high",
            confirm="" if low else (found[0]["name"] if count == 1 else f"apply to {count} devices"),
        )
        change_store.save(record)

        caveat = ""
        if record.render_method == FROM_REVIEWED_CLI:
            caveat = (
                "\n\n**These commands were not computed against the devices.** This "
                "entry's modules cannot report what they would do, so the text above "
                "is CLI a person reviewed in advance. It does not account for the "
                "devices' current state."
            )

        listing = "\n".join(f"  {line}" for line in record.commands)
        self._offer_the_decision(record, bool(caveat))
        return Response(
            message=(
                f"Change `{record.id}` drafted and awaiting approval.\n\n"
                f"Capability: `{entry.name}`\nDevices: `{devices}`\n"
                f"Commands ({record.render_method}):\n```\n{listing}\n```"
                f"{caveat}\n\n"
                "I cannot approve this myself. You, as the requesting engineer, have "
                "to read those commands and approve them on the card in this chat "
                "before it can run."
            ),
            break_loop=False,
        )

    def _offer_the_decision(self, record: Any, caveat: bool) -> None:
        """Put the approval in front of an approver as a card, not only as prose.

        The card calls `change_approve`, which checks `network.approve`; it is not
        the agent asking a question. A reader with chat access and no approver
        role sees the same card and the endpoint refuses them, which is the right
        way round -- the button is a convenience over an authorisation that
        already exists, never the authorisation itself.

        Best effort on purpose. The change is saved and the prose above says what
        was drafted; failing to draw a button must not be the thing that loses a
        change record.
        """
        show_card(
            self.agent,
            record,
            f"Apply `{record.playbook_name}` on {record.limit}?",
            f"{record.summary} — approving binds to these commands as they stand now."
            + (" These were not computed against the devices." if caveat else ""),
        )

    def _status(self, args: dict[str, Any]) -> Response:
        change_id = str(args.get("change_id") or "").strip()
        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return Response(message=str(error), break_loop=False)
        drifted = record.approved_hash and record.approved_hash != record.current_hash
        note = "\nThe plan has changed since it was approved." if drifted else ""
        return Response(
            message=(
                f"Change `{record.id}` is **{record.status}**.\n"
                f"Capability: `{record.playbook_name}` on `{record.limit}`\n"
                f"Approved by: {record.approved_by or 'nobody yet'}{note}"
            ),
            break_loop=False,
        )

    def _list(self) -> Response:
        records = change_store.list_records()
        if not records:
            return Response(message="No network changes on record.", break_loop=False)
        lines = [
            f"- `{r.id}` **{r.status}** — {r.playbook_name} on {r.limit}"
            for r in records[:20]
        ]
        return Response(message="\n".join(lines), break_loop=False)

    def _cancel(self, args: dict[str, Any]) -> Response:
        change_id = str(args.get("change_id") or "").strip()
        try:
            record = change_store.load(change_id)
        except change_store.ChangeNotFound as error:
            return Response(message=str(error), break_loop=False)
        if not record.is_open:
            return Response(
                message=f"Change {record.id} is already {record.status}.", break_loop=False
            )
        record.status = CANCELLED
        change_store.save(record)
        return Response(message=f"Change `{record.id}` cancelled.", break_loop=False)


def show_card(agent: Any, record: ChangeRecord, title: str, consequence: str) -> None:
    """Draw a change's (or a rollback's) decision card; never raises (see `_offer_the_decision`)."""
    try:
        from helpers.parallel_tools import user_facing_context

        kvps = change_card(record, title, consequence)
        user_facing_context(agent).log.log(
            type="info", heading=kvps["question"], content="\n".join(record.commands), kvps=kvps
        )
    except Exception:  # noqa: BLE001
        return


def change_card(record: ChangeRecord, title: str, consequence: str) -> dict[str, str]:
    """The decision card for a change or rollback record, built from the record alone."""
    from plugins._ea_interaction.helpers import cards

    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    rows = [("Capability", record.playbook_name)]
    for d in record.devices_resolved:
        facts = (d.get("mgmt_ip"), d.get("network"), d.get("site"), d.get("platform"), d.get("credential"))
        rows.append((str(d.get("name")), " · ".join(str(f) for f in facts if f)))
    undo = record.kind == "rollback"
    return cards.decision_card(
        title,
        endpoint="plugins/_ea_ansible/change_approve",
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        approve_label="Roll back" if undo else "Approve",
        reject_label="Keep" if undo else "Reject",
        consequence=consequence,
        details=rows,
        commands=record.commands,
        checks=[{"label": c["label"], "ok": c["ok"], "detail": c.get("detail", "")} for c in record.preflight.get("checks", [])],
        diff=render.diff_lines(record.diff),
        rollback=None if undo else record.rollback_commands or ["No rollback: this has to be undone by hand."],
        expires_at=record.expires_at,
        tier=record.tier,
        confirm=record.confirm,
    )
