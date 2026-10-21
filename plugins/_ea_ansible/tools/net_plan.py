"""A runbook plan: several steps, approved once, run in order (ADR 0053).

`request` takes up to eight ordered steps, each a read or a change, and validates every one
exactly as `net_read` / `net_change` would alone: active Inventory devices, the same
patterns and pre-check, placeholders filled. One card lists every step, its commands, the
checks and the rollback of each change; the plan is a `kind="plan"` record and approval
binds to the hash of the whole of it. `run` re-renders the changes, then executes the
steps in order and stops at the first failure or failed verify, offering one rollback
card (another plan record, undone in reverse order) for the changes already applied.
Nothing in the `read_approval` setting covers a plan: it always asks.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import Any

from helpers import user_scope
from helpers.tool import Response, Tool

from plugins._ea_ansible.helpers import bundles, catalogue, change_store, conditions, executor_client, failures
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import excerpt as excerpts
from plugins._ea_ansible.helpers import plans, preflight, render
from plugins._ea_ansible.helpers.change_model import (
    APPLIED,
    APPROVED,
    AWAITING_APPROVAL,
    CANCELLED,
    CLOSED,
    EXPIRED,
    FAILED,
    ROLLED_BACK,
    SUPERSEDED,
    VERIFY_FAILED,
    ChangeRecord,
)
from plugins._ea_ansible.tools import net_apply
from plugins._ea_ansible.tools.net_change import EXPIRY_SECONDS, LOW_RISK_MAX_DEVICES
from plugins._ea_ansible.tools.net_read import MAX_DEVICES, RESERVED, SHOW_RULE, _resolve
from plugins._ea_inventory.helpers import redact

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]


class Refused(Exception):
    """A step that cannot be part of the plan; the message says why."""


class NetPlan(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})
        action = str(args.get("action") or "request").strip().lower()
        if not plugin_config.is_configured(self.agent):
            return _say("Network automation is not configured on this instance. An administrator must set the Ansible executor address and token.")
        if action == "request":
            return self._request(args)
        if action == "run":
            return self._run(args)
        return _say(f"Unknown action '{action}'. Use request or run.")

    # --- request: validate every step, ask once ------------------------------

    def _request(self, args: dict[str, Any]) -> Response:
        raw = args.get("steps")
        if not isinstance(raw, list) or not 1 <= len(raw) <= plans.MAX_STEPS:
            return _say(f"A plan takes 1 to {plans.MAX_STEPS} steps in a `steps` list.")
        steps: list[dict[str, Any]] = []
        devices: dict[str, dict[str, Any]] = {}
        checks: list[dict[str, Any]] = []
        tiers: list[str] = []
        caveat = False
        try:
            entries = catalogue.load()
            commit = str(executor_client.health(self.agent).get("catalogue_commit") or "")
            for n, step_args in enumerate(raw, 1):
                try:
                    step, tier, found, passed, reviewed = self._step(step_args, entries, commit)
                except (catalogue.UnknownEntry, catalogue.CatalogueError, catalogue.VariableRejected,
                        render.RenderFailed, Refused) as error:
                    return _say(f"Step {n} cannot be part of the plan, so nothing was requested: {error}")
                steps.append(step)
                tiers.append(tier)
                caveat = caveat or reviewed
                devices.update({d["name"]: d for d in found})
                checks.append({"label": f"step {n}: pre-checks", "ok": True, "detail": f"{passed} passed"})
        except executor_client.ExecutorError as error:
            return _say(f"The pre-check could not run, so no plan was made: {error}")

        tier = plans.tier_of(tiers)
        commands, undo = plans.listing(steps)
        record = ChangeRecord(
            kind="plan",
            requested_by=user_scope.get_current_user_id() or "",
            status=AWAITING_APPROVAL,
            playbook_name="plan",
            commit=commit,
            limit=",".join(devices),
            summary=f"{len(steps)}-step runbook plan",
            commands=commands,
            rollback_commands=undo,
            render_method="plan",
            expires_at=time.time() + EXPIRY_SECONDS,
            preflight={"checks": checks},
            devices_resolved=list(devices.values()),
            tier=tier,
            confirm=plans.confirm_for(tier, steps),
            steps=steps,
        )
        # Built before it is saved: a plan too long for one card is refused, never shown cut short.
        try:
            plans.card(record, "x", "x")
        except Exception as error:  # noqa: BLE001 - CardRejected says what was too long
            return _say(f"The plan does not fit on one card ({error}). Use fewer or shorter steps, or split it.")
        change_store.save(record)
        try:
            self._show(record, f"Run this {len(steps)}-step plan on {record.limit}?", _consequence(tier, caveat))
        except Exception as error:  # noqa: BLE001 - the card is the only way to approve
            record.status = CANCELLED
            change_store.save(record)
            return _say(f"The approval card could not be shown, so the plan was cancelled: {error}")
        return _say(
            f"Plan `{record.id}` ({len(steps)} steps on `{record.limit}`) is waiting for the engineer's "
            "approval on the card in the chat. Nothing has run. Stop here and wait: do not call any other "
            f"tool. If they answer Approve, call net_plan with action=run and change_id={record.id}; if "
            "they answer Reject, stop. You cannot approve it yourself."
        )

    def _step(self, args: Any, entries: dict[str, Any], commit: str) -> tuple[dict[str, Any], str, list[dict[str, Any]], int, bool]:
        """One step, validated as it would be alone: (step, tier, devices, checks passed, from reviewed CLI)."""
        if not isinstance(args, dict):
            raise Refused("a step is an object with a capability (or bundle) and devices.")
        args = dict(args)
        names = [t for t in re.split(r"[,\s]+", str(args.get("devices") or "")) if t]
        if not names:
            raise Refused("it has to name the devices it targets.")
        if len(names) > MAX_DEVICES:
            raise Refused(f"ask for at most {MAX_DEVICES} devices per step.")
        bundle = str(args.get("bundle") or "").strip()
        entry = catalogue.resolve("reads/show" if bundle else str(args.get("capability") or "").strip(), entries)
        if entry.kind not in ("read", "change"):
            raise Refused(f"'{entry.name}' is a rollback; it is offered after a change, not planned.")
        found, refusal = _resolve(names)
        if refusal:
            raise Refused(refusal)

        if entry.kind == "read":
            free_form = "command_1" in entry.variables
            if bundle:
                if args.get("commands"):
                    raise Refused("pass a bundle or your own commands, not both.")
                args["commands"], refusal = bundles.commands_for(bundle, {d["platform"] for d in found}, entry)
                if refusal:
                    raise Refused(refusal)
            supplied = {k: v for k, v in args.items() if k not in RESERVED}
            if free_form and args.get("commands"):
                listed = args["commands"]
                listed = listed.splitlines() if isinstance(listed, str) else list(listed)
                supplied.update({f"command_{i}": c.strip() if isinstance(c, str) else c
                                 for i, c in enumerate([c for c in listed if c], 1)})
            try:
                variables = entry.validate_variables(supplied)
            except catalogue.VariableRejected as error:
                raise Refused(f"{error} {SHOW_RULE}" if free_form else str(error)) from error
            commands, refusal = bundles.read_lines(entry, found, variables)
            if refusal:
                raise Refused(refusal)
            asks, rollback, rollback_commands, reviewed, tier = entry, "", [], False, "read"
        else:
            variables = entry.validate_variables({k: v for k, v in args.items() if k not in ("capability", "devices")})
            _, _, rollback_commands = render.rollback_plan(entries, entry.rollback, variables)
            # As in net_change: the entry's platforms stand in for the read commands a pre-check asks about.
            asks = SimpleNamespace(name=entry.name, platforms=entry.platforms,
                                   read_commands={p: (entry.name,) for p in entry.platforms})
            rollback = entry.rollback
            tier = "change-low" if entry.risk == "low" and len(found) <= LOW_RISK_MAX_DEVICES else "change-high"

        checked = preflight.run_preflight(self.agent, found, asks)
        if not checked["ok"]:
            failed = "; ".join(f"{c['label']}: {c['detail']}" for c in checked["checks"] if not c["ok"])
            raise Refused(f"`{entry.name}` on `{', '.join(names)}` cannot run right now ({failed}). If a credential is missing, use net_credential.")
        if entry.kind == "change":
            plan = render.render(self.agent, entry, variables, ",".join(d["name"] for d in found), commit)
            commands, reviewed = plan["commands"], plan["render_method"] == "reviewed_cli"
            if not commands:
                raise Refused(f"`{entry.name}` would change nothing on `{', '.join(names)}`: they already match. Drop the step.")
        step = {
            "kind": entry.kind, "capability": entry.name, "variables": variables,
            "limit": ",".join(d["name"] for d in found), "commands": commands,
            "rollback": rollback or "", "rollback_commands": rollback_commands,
        }
        return step, tier, checked["devices"], len(checked["checks"]), reviewed

    def _show(self, record: ChangeRecord, title: str, consequence: str) -> None:
        from helpers.parallel_tools import user_facing_context

        kvps = plans.card(record, title, consequence)
        user_facing_context(self.agent).log.log(type="info", heading=kvps["question"], content="\n".join(record.commands), kvps=kvps)

    # --- run: only what was approved -----------------------------------------

    def _run(self, args: dict[str, Any]) -> Response:
        actor = user_scope.get_current_user_id() or ""
        try:
            record = change_store.load(str(args.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return _say(str(error))
        if record.kind != "plan":
            return _say(f"`{record.id}` is not a plan. Reads run through net_read, changes through net_apply.")
        if record.requested_by != actor:
            return _say(f"Plan `{record.id}` was not made by the current user, so it will not run.")
        if record.status == AWAITING_APPROVAL and record.expired():
            record.status = EXPIRED
            change_store.save(record)
        if record.status != APPROVED:
            return _say(
                f"Plan `{record.id}` is '{record.status}', not approved, so nothing ran. If it is awaiting "
                "approval, tell the user to press Approve on its card (typing 'approve' in chat does not "
                "approve it), then stop and wait."
            )
        refusal = conditions.first_failure(record, actor)  # hash unchanged, approver, not expired
        if refusal:
            return _say(refusal)

        # Re-render every change before anything runs, as net_apply does for one: if the devices moved
        # since the engineer read the commands, the plan is superseded, not quietly applied.
        entries = catalogue.load()
        try:
            for n, s in enumerate(record.steps, 1):
                if s["kind"] == "change":
                    fresh = render.render(self.agent, catalogue.resolve(s["capability"], entries),
                                          s["variables"], s["limit"], record.commit)
                    if fresh["commands"] != s["commands"]:
                        record.status = SUPERSEDED
                        change_store.save(record)
                        return _say(f"Step {n} would now send different commands than the engineer approved, so nothing ran. The plan is superseded: request it again.")
        except (catalogue.CatalogueError, catalogue.UnknownEntry, render.RenderFailed, executor_client.ExecutorError) as error:
            return _say(f"The plan could not be re-checked, so nothing ran: {error}")

        if not change_store.take(record):  # one statement in the approval register: an approval runs once
            return _say(f"Plan `{record.id}` has no approval left to use (it already ran, or is running), so nothing ran.")
        report: list[str] = []
        applied: list[tuple[int, dict[str, Any]]] = []
        stopped = ""
        for n, s in enumerate(record.steps, 1):
            outcome, reached, text = self._run_step(record, s, entries)
            report.append(f"# step {n}: {s['capability']} on {s['limit']}: {'ok' if outcome == APPLIED else 'STOPPED HERE'}\n{text}")
            if reached:
                applied.append((n, s))
            if outcome != APPLIED:
                stopped = outcome
                break

        record.status = stopped or (APPLIED if any(s["kind"] == "change" for s in record.steps) else CLOSED)
        record.applied_at, record.output_excerpt = time.time(), "\n\n".join(report)[-6000:]
        if stopped:
            record.error = f"Stopped at step {len(report)} of {len(record.steps)}."
        change_store.save(record)
        if audit is not None:
            audit.emit(
                "network.plan.run", change_id=record.id, requested_by=record.requested_by,
                approved_by=record.approved_by, approved_hash=record.approved_hash,
                steps=len(record.steps), completed=len(report) - bool(stopped), status=record.status,
            )

        tail = ""
        if record.rollback_of and not stopped:
            original = change_store.load(record.rollback_of)
            original.status = ROLLED_BACK
            change_store.save(original)
        elif stopped and not record.rollback_of:
            tail = self._offer_rollback(record, applied, entries)
        elif stopped:
            tail = "The rollback did not finish cleanly: the rest has to be undone by hand."
        head = (f"Plan `{record.id}` **stopped** at step {len(report)} of {len(record.steps)}; later steps did not run."
                if stopped else f"Plan `{record.id}` ran all {len(record.steps)} steps.")
        return _say(f"{head}\n\n{record.output_excerpt}\n\n{tail}")

    def _run_step(self, record: ChangeRecord, s: dict[str, Any], entries: dict[str, Any]) -> tuple[str, bool, str]:
        """(APPLIED, FAILED or VERIFY_FAILED; reached a device with a change; redacted text).

        A read runs in check mode, a change for real."""
        change = s["kind"] == "change"
        try:
            result = executor_client.run(
                self.agent, playbook_name=s["capability"], commit=record.commit, variables=s["variables"],
                limit=s["limit"], check_mode=not change,
            )
        except executor_client.JobStillRunning as error:
            return FAILED, change, f"Outcome unknown, it may still be applied: {error}"  # so its undo is offered
        except executor_client.ExecutorError as error:
            return FAILED, False, f"Did not run: {error}"
        text = redact.redact_text(excerpts.excerpt(result.output, 3000))[0]
        if not result.succeeded:
            found = failures.classify(result.output)
            return FAILED, change, f"Failed: **{found['code']}** — {found['cause']}\n```\n{text}\n```"
        if not change:
            return APPLIED, False, f"```\n{text}\n```"
        # The entry's own verify, through the same code net_apply uses for a single change.
        step = ChangeRecord(
            variables=s["variables"], commit=record.commit, limit=s["limit"],
            devices_resolved=[d for d in record.devices_resolved if d["name"] in s["limit"].split(",")],
        )
        proof = net_apply.NetApply._prove(SimpleNamespace(agent=self.agent), step, catalogue.resolve(s["capability"], entries))
        return (VERIFY_FAILED if step.verified is False else APPLIED), True, f"```\n{text}\n```\n{proof}"

    def _offer_rollback(self, record: ChangeRecord, applied: list[tuple[int, dict[str, Any]]], entries: dict[str, Any]) -> str:
        """Save the undo of the changes already applied as a plan of its own, and put its card on screen."""
        undo_steps, by_hand = [], []
        for n, s in reversed(applied):
            try:
                entry, variables, commands = render.rollback_plan(entries, s["rollback"], s["variables"])
            except (catalogue.CatalogueError, catalogue.UnknownEntry, catalogue.VariableRejected):
                by_hand.append(n)
                continue
            undo_steps.append({
                "kind": "change", "capability": entry.name, "variables": variables, "limit": s["limit"],
                "commands": s["rollback_commands"] or commands, "rollback": "", "rollback_commands": [],
            })
        hand = f" Steps {', '.join(map(str, by_hand))} have no reviewed rollback and must be undone by hand." if by_hand else ""
        if not undo_steps:
            return "No change was applied that has a reviewed rollback." + hand
        commands, _ = plans.listing(undo_steps)
        tier = record.tier if record.tier != "read" else "change-low"
        undo = ChangeRecord(
            kind="plan", requested_by=record.requested_by, status=AWAITING_APPROVAL, playbook_name="plan",
            commit=record.commit, limit=record.limit, summary=f"Undo plan {record.id}", commands=commands,
            render_method="plan", expires_at=time.time() + EXPIRY_SECONDS, preflight=record.preflight,
            devices_resolved=record.devices_resolved, tier=tier, confirm=plans.confirm_for(tier, undo_steps),
            steps=undo_steps, rollback_of=record.id,
        )
        change_store.save(undo)
        record.rollback_id = undo.id
        change_store.save(record)
        try:
            self._show(undo, f"Roll back the applied steps of plan {record.id}?",
                       f"Undoes {len(undo_steps)} change(s) in reverse order. Approving binds to these commands as they stand now.")
        except Exception:  # noqa: BLE001 - the record is saved; failing to draw a button must not lose it
            pass
        return (f"A 'Roll back?' card for the applied steps is on screen (rollback plan `{undo.id}`). Do not run it "
                f"unless the engineer approves it; then call net_plan with action=run and change_id={undo.id}.{hand}")


def _consequence(tier: str, caveat: bool) -> str:
    text = ("Read-only: nothing on a device changes." if tier == "read"
            else "Runs every step in order and stops at the first failure; each change is checked after it is applied.")
    text += " Approving binds to these steps and commands as they stand now."
    return text + (" Some commands were not computed against the devices." if caveat else "")


def _say(message: str) -> Response:
    return Response(message=message, break_loop=False)
