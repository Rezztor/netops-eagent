"""What the Azure change tools, endpoints and gate share about a change record.

The record itself is `_ea_ansible/helpers/change_model.ChangeRecord` (one ledger, one audit path). Here: the guard rails
as the application sees them, the CR/SR number check, the change document files, how a plan becomes a record, and what
happens after an apply (the inventory pull, the list of what the product made, the second document).

Nothing here deletes, stops or restarts anything in Azure, and nothing here can: the executor client has no such call.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

from helpers import db, plugins, user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import (
    APPLIED,
    AWAITING_APPROVAL,
    CLOSED,
    FAILED,
    FROM_REVIEWED_CLI,
    SUPERSEDED,
    VERIFY_FAILED,
    ChangeRecord,
)
from plugins._ea_azure_change.helpers import document, executor_client, gate, tier
from plugins._ea_inventory.helpers import redact

log = logging.getLogger(__name__)

PLUGIN_NAME = executor_client.PLUGIN_NAME
KINDS = ("azure_change", "azure_action")
#: The only things a plan may do to an Azure resource. Anything else is refused here as well as in the executor.
ALLOWED_ACTIONS = frozenset({"create", "update", "read", "no-op", "import"})
REFERENCE_MAX = 40
PATTERN_MAX = 100
DAY = 86400
_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_FILE = _ROOT / "cloud_executor" / "templates.yaml"
RUNBOOKS_FILE = Path(__file__).resolve().parents[1] / "runbooks.yaml"

#: The same values as `default_config.yaml` (a test holds them equal); what applies when nothing is saved.
RAIL_DEFAULTS: dict[str, Any] = {
    "subscriptions": [],
    "usd_month_per_change": 250,
    "usd_month_total": 1000,
    "low_risk_usd_month": 20,
    "max_resources": 40,
    "allow_free_code": False,
    "allow_unknown_price": False,
    "reference_pattern": "(CR|SR)-?[0-9]{4,10}",
    "document_valid_days": 14,
}
#: Free code the card can show whole: one kvps value is cut at `cards.VALUE_BUDGET` (a test holds them equal). A change
#: whose code does not fit is refused, never cut, so what an engineer approves is what they read.
CODE_MAX = 5000
FREE_BACKOUT = ("Free code has no reviewed back-out. The engineer decides, outside the product and after their own approval, "
                "how to undo what it made. The product never deletes, stops or restarts anything.")
EXAMPLES = ("CR-12345", "SR-12345", "CR12345", "SR-123456", "CHG-12345")

#: Shown when `runbooks.yaml` has no entry for a kind of suggestion. Reviewed text: no step runs anything.
GENERIC_RUNBOOK = {
    "title": "Remove or stop by hand",
    "steps": [
        "Open {names} in the Azure portal and confirm it is what the finding says: check its Activity log, its metrics "
        "and its dependencies for the last 30 days.",
        "Ask the owner named in its tags, or the owner of its resource group, to confirm in writing that it is not needed.",
        "Take the change through your own change process and obtain the CR or SR number.",
        "Keep what you may need later: a snapshot, an export of its configuration, a copy of its template.",
        "Make the change yourself in the portal or with the Azure CLI, after your own approval. The product will not.",
        "Pull the Azure inventory again and confirm the next pull shows the new state.",
    ],
}


def enabled() -> bool:
    return plugins.get_toggle_state(PLUGIN_NAME) == "enabled"


def rails(agent: Any | None = None) -> dict[str, Any]:
    cfg = plugins.get_plugin_config(PLUGIN_NAME, agent=agent) or {}
    saved = cfg.get("guardrails") if isinstance(cfg.get("guardrails"), dict) else {}
    return {**RAIL_DEFAULTS, **saved}


def valid_days(agent: Any | None = None) -> int:
    try:
        return min(30, max(1, int(rails(agent)["document_valid_days"])))
    except (TypeError, ValueError):
        return RAIL_DEFAULTS["document_valid_days"]


# --- the CR or SR number -----------------------------------------------------------------------------------


def pattern_problem(pattern: Any) -> str | None:
    """Why a reference pattern may not be saved or used, or None."""
    text = str(pattern or "")
    if not text.strip():
        return "The reference pattern is empty."
    if len(text) > PATTERN_MAX:
        return f"The reference pattern is longer than {PATTERN_MAX} characters."
    try:
        compiled = re.compile(text, re.IGNORECASE)
    except re.error as error:
        return f"The reference pattern does not compile: {error}."
    if compiled.fullmatch(""):
        return "The reference pattern matches an empty number, so it would accept anything."
    return None


def example_for(pattern: str) -> str:
    return next((e for e in EXAMPLES if re.fullmatch(pattern, e, re.IGNORECASE)), "")


def reference_problem(value: Any, agent: Any | None = None) -> str | None:
    """Why this is not an acceptable CR or SR number as the pattern stands now, or None. Fails closed."""
    ref = str(value or "").strip()
    if not ref:
        return "A CR or SR number is needed to approve this change. Enter the number the change process gave you."
    pattern = str(rails(agent)["reference_pattern"])
    if pattern_problem(pattern):
        return f"{pattern_problem(pattern)} An administrator must fix it in Settings before any Azure change can be approved."
    if len(ref) > REFERENCE_MAX or not re.fullmatch(pattern, ref, re.IGNORECASE):
        example = example_for(pattern)
        return f"`{ref[:REFERENCE_MAX]}` is not a CR or SR number in this organisation's format" + (f", for example {example}." if example else ".")
    return None


def normalise_reference(value: Any) -> str:
    return str(value or "").strip().upper()


# --- the catalogue and the workspace -------------------------------------------------------------------------


def templates() -> dict[str, dict[str, Any]]:
    """The reviewed template entries by name, from the executor's catalogue file shipped beside this code."""
    try:
        data = yaml.safe_load(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return {e["name"]: e for e in data or [] if isinstance(e, dict) and e.get("name")}


def catalogue_text() -> str:
    """The templates and what each needs, as the list a wrong template name or step gets back."""
    lines = []
    for name, e in sorted(templates().items()):
        needs = ", ".join(f"{v}{' (optional)' if s.get('optional') else ''}" for v, s in (e.get("variables") or {}).items()) or "nothing"
        lines.append(f"- {name}: {e.get('summary', '')} Needs: {needs}.")
    return "\n".join(lines) or "- none"


def workspace_of(limit: str) -> dict[str, str]:
    sub, _, group = str(limit).partition("/")
    return {"subscription": sub, "resource_group": group}


def describe_values(variables: dict[str, Any]) -> list[str]:
    """The template values as `name = <JSON>` lines: what the card and the document show, and `lines_to_variables` reads back."""
    return [f"{k} = {json.dumps(v, ensure_ascii=False, sort_keys=True)}" for k, v in sorted(variables.items())]


def lines_to_variables(lines: Any) -> dict[str, Any]:
    """Inverse of `describe_values` for an edited card; a line that is not `name = value` is refused with a ValueError."""
    listed = lines.splitlines() if isinstance(lines, str) else list(lines or [])
    out: dict[str, Any] = {}
    for line in (str(x).strip() for x in listed):
        if not line:
            continue
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", name):
            raise ValueError(f"`{line[:60]}` is not a `name = value` line.")
        try:
            out[name] = json.loads(value.strip())
        except ValueError:
            out[name] = value.strip()
    return out


def _one_line(text: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


# --- the change documents ------------------------------------------------------------------------------------


def documents_dir(user_id: str) -> str:
    return user_scope.ensure_user_dir(change_store.DIR_NAME, "documents", user_id=user_id)


def document_path(record: ChangeRecord, meta: dict[str, Any]) -> str:
    """Where a document of this record lives: its owner's directory, the bare file name from the record."""
    return os.path.join(documents_dir(record.requested_by), os.path.basename(str(meta.get("filename") or "")))


def file_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def display_names(record: ChangeRecord) -> dict[str, str]:
    """What the document prints instead of ids: the requester's and approver's username, the subscription's stored name.

    A name that cannot be found is left out, and the document falls back to the id (a user who is gone, no inventory yet).
    """
    from helpers import users
    from plugins._ea_azure.helpers import store

    out: dict[str, str] = {}
    for key, uid in (("requested_by_name", record.requested_by), ("approved_by_name", getattr(record, "approved_by", ""))):
        try:
            user = users.get_user_by_id(uid) if uid else None
        except Exception:  # noqa: BLE001 - a lookup must never stop the document
            user = None
        if user and user.get("username"):
            out[key] = str(user["username"])
    sub = workspace_of(record.limit)["subscription"].lower()
    try:
        out["subscription_name"] = next((s["name"] for s in store.subscriptions() if s["native_id"] == sub and s["name"]), "")
    except Exception:  # noqa: BLE001
        pass
    return out


def write_documents(record: ChangeRecord, version: str, extra: dict[str, Any] | None = None) -> None:
    """Write a version once into the requester's directory and list its files on the record (not saved here)."""
    extra = {**display_names(record), **(extra or {})}
    metas = document.write_once(record, version, documents_dir(record.requested_by), extra)
    for meta in metas:
        meta["plan_digest"] = record.plan_digest
    record.documents = [*record.documents, *metas]


def audit_document(record: ChangeRecord, version: str) -> None:
    """`azure.change.document_issued`: the digest and the file's SHA-256, never the document."""
    if change_store.audit is None:
        return
    prefix = "azure.action" if record.kind == "azure_action" else "azure.change"
    pdf = next((d for d in record.documents if d.get("version") == version and d.get("format") == "pdf"), {})
    change_store.audit.emit(f"{prefix}.document_issued", change_id=record.id, version=version, plan_digest=record.plan_digest,
                            document_sha256=pdf.get("sha256", ""), requested_by=record.requested_by, reference=record.reference)


#: The order on the card, numbered there as well. Every sentence that tells the engineer what to do says this, in this order.
STEPS = ("1. Download the change document from the card (PDF or Word). 2. Enter the CR or SR number in the field on the card. "
         "3. Press Approve (it stays off until the document is downloaded and the number is in the right format).")


def pass_on(reason: str) -> str:
    """A refusal as the agent receives it: to be told to the engineer as written, with the card's steps and no others."""
    return f"{reason}\n\nTell the user this reason as written, then the steps on the card, in this order: {STEPS} Do not add or invent other steps."


def issued_document_problem(record: ChangeRecord) -> str | None:
    """The `document_issued` condition: an issued document for this digest, downloaded, every file still as written."""
    issued = [d for d in record.documents if d.get("version") == "issued"]
    if not issued or not record.plan_digest or any(d.get("plan_digest") != record.plan_digest for d in issued):
        return "There is no change document issued for this plan, so it cannot run. Draft it again to get one."
    if not any(d.get("downloaded_at") for d in issued):
        return "The change document for this plan was never downloaded. Step 1 on the card is to download it; then the number (step 2) and Approve (step 3)."
    for meta in issued:
        try:
            if file_sha256(document_path(record, meta)) != meta.get("sha256"):
                return "A change document file no longer matches what was issued, so it cannot be relied on. Draft the change again."
        except OSError:
            return "A change document file is missing, so it cannot be relied on. Draft the change again."
    return None


# --- from a plan to a record ----------------------------------------------------------------------------------


class PlanRefused(Exception):
    """A plan the product will not turn into a card. The message is the sentence for the agent."""


def check_plan_answer(answer: dict[str, Any], ceiling: float | None = None) -> None:
    """Defence in depth on the executor's answer: nothing is removed, the detail is there, no check failed."""
    if not answer.get("ok"):
        raise PlanRefused(f"The plan was refused at the '{answer.get('stage') or 'plan'}' stage: {answer.get('refusal') or 'no reason given'}")
    summary = answer.get("summary") or {}
    resources = summary.get("resources") or []
    bad = sorted({str(r.get("action")) for r in resources if str(r.get("action")) not in ALLOWED_ACTIONS})
    if bad:
        raise PlanRefused(f"The plan contains an action the product never performs ({', '.join(bad)}), so it was refused.")
    if int(summary.get("add") or 0) + int(summary.get("change") or 0) + int(summary.get("import") or 0) and not resources:
        raise PlanRefused("The plan did not list its resource changes, so it cannot be checked and was refused.")
    if not answer.get("plan_digest"):
        raise PlanRefused("The plan carries no digest, so there is nothing to approve.")
    failed = [c for c in answer.get("checks") or [] if not c.get("ok") and c.get("level") != "warn"]
    if failed:
        raise PlanRefused("A check failed: " + "; ".join(f"{c.get('label')}: {c.get('detail') or 'failed'}" for c in failed))
    monthly = (answer.get("cost") or {}).get("monthly_usd")
    if ceiling is not None and monthly is not None and float(monthly) > float(ceiling):
        raise PlanRefused(f"About {monthly} USD a month is over the {ceiling} USD ceiling for one change.")


def nothing_to_change(answer: dict[str, Any]) -> bool:
    summary = answer.get("summary") or {}
    return not sum(int(summary.get(k) or 0) for k in ("add", "change", "import"))


class NothingToChange(PlanRefused):
    """The plan adds, changes and imports nothing: Azure already matches."""


_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_GROUP = re.compile(r"[A-Za-z0-9._()-]{1,90}")


def resolve_subscription(value: Any) -> tuple[str, str | None]:
    """`(guid, None)` for a subscription named by its GUID or by its stored name, or `("", problem)`. An unknown or
    ambiguous name lists the stored subscriptions with their GUIDs so the model can pick (a subscription ID is not a
    secret). This is how every subscription argument is read, so a name no longer forces the model to find the GUID."""
    text = str(value or "").strip()
    if not text:
        return "", "`subscription` is needed: the subscription's name or its GUID, as the Azure inventory lists it."
    if _GUID.fullmatch(text):
        return text.lower(), None
    try:
        from plugins._ea_azure.helpers import store

        subs = store.subscriptions()
    except Exception:  # noqa: BLE001 - no inventory module or no database: the GUID is still accepted above
        subs = []
    hits = [s for s in subs if text.lower() in (str(s["name"]).lower(), str(s["native_id"]).lower())]
    if len(hits) == 1:
        return str(hits[0]["native_id"]).lower(), None
    listing = "; ".join(f"{s['name']} ({s['native_id']})" for s in subs) or "none are stored yet (pull the Azure inventory first)"
    if not hits:
        return "", f"No subscription `{text}` is in the stored Azure inventory. Give one by name or GUID. Known subscriptions: {listing}."
    return "", f"`{text}` matches more than one stored subscription; give its GUID. Known subscriptions: {listing}."


def scope_problem(subscription: str, resource_group: str) -> str | None:
    if not _GUID.fullmatch(subscription):
        return "`subscription` must be the subscription's GUID, as the Azure inventory lists it."
    if not _GROUP.fullmatch(resource_group):
        return "`resource_group` must be a resource group name: letters, digits and . _ ( ) - only, 90 characters at most."
    return None


def free_code_problem(code: Any, agent: Any | None = None) -> str | None:
    """Why this free code may not be drafted, or None. The switch, its size and characters, then the executor's own static
    check (the same file), so the refusal comes before a plan is asked for."""
    if rails(agent)["allow_free_code"] is not True:
        return ("Free code is switched off on this instance. An administrator with the plugins.manage permission can turn it on in "
                "Settings, Azure changes, guard rails, Allow free code. Draft from a template, or write a suggestion for the engineer.")
    if not isinstance(code, str) or not code.strip():
        return "`code` is one text of OpenTofu (HCL)."
    if len(code) > CODE_MAX:
        return f"The code is {len(code)} characters; the approval card shows at most {CODE_MAX}, and nothing is approved that is not shown. Split it into smaller changes."
    if any(ord(c) < 32 and c not in "\n\t" for c in code):
        return "The code holds a control character (a carriage return, for one), which is not allowed. Use plain line feeds."
    refusals = gate.check_code({"main.tf": code})
    return f"The code was refused before any plan was made: {refusals[0]}" if refusals else None


def code_of(record: ChangeRecord) -> str | None:
    """The free code of a record (its `commands` lines, joined as they were split), or None for a template change."""
    return "\n".join(record.commands) if record.playbook_name == "free" else None


def source_of(record: ChangeRecord) -> dict[str, Any]:
    """What the executor is told to plan and apply: the code or the template with its values."""
    code = code_of(record)
    return {"code": code} if code is not None else {"template": record.playbook_name, "variables": record.variables}


def ask_plan(agent: Any, *, template: str, variables: dict[str, Any], subscription: str, resource_group: str,
             change_id: str, requester: str, code: str | None = None) -> tuple[dict[str, Any], str]:
    """The executor's plan and the template catalogue commit it reports. Network only: run it off the event loop.
    `code` (free code) replaces the template and its values."""
    commit = str(executor_client.health(agent).get("templates_commit") or "")
    if not commit:
        raise PlanRefused("The cloud executor reports no template catalogue commit, so nothing can be pinned to a reviewed version.")
    answer = executor_client.plan(agent, source={"code": code} if code is not None else {"template": template, "variables": variables},
                                  workspace={"subscription": subscription, "resource_group": resource_group},
                                  change_id=change_id, actor=requester)
    return answer, commit


def build_record(agent: Any, answer: dict[str, Any], commit: str, *, change_id: str, requester: str, template: str,
                 variables: dict[str, Any], subscription: str, resource_group: str, purpose: str,
                 planned_note: str = "", code: str | None = None) -> ChangeRecord:
    """Accept the executor's answer (or raise `PlanRefused`) and make the record, not saved. `code` makes it a free-code record."""
    check_plan_answer(answer, rails(agent)["usd_month_per_change"])
    from plugins._ea_azure_change.helpers import drift  # here, not at the top: drift imports this module

    if problem := drift.draft_problem(subscription, resource_group):  # unaccepted drift in this workspace (phase 7)
        raise PlanRefused(problem)
    if nothing_to_change(answer):
        raise NothingToChange(f"{'The code' if code is not None else '`' + template + '`'} would change nothing in {resource_group}: Azure already matches. No change drafted.")
    return make_record(change_id=change_id, requester=requester, template=template, variables=variables, subscription=subscription,
                       resource_group=resource_group, purpose=purpose, answer=answer, commit=commit, agent=agent, planned_note=planned_note,
                       advice=advice_for(purpose, variables, subscription), code=code)


def advice_for(purpose: str, variables: dict[str, Any], subscription: str) -> list[dict[str, Any]]:
    """The warn-level advice findings for this change, from the stored inventory. Best effort: never stops a draft."""
    try:
        from plugins._ea_azure.helpers import advise, store

        if not store.as_of().get("last_pull"):
            return []
        region = re.sub(r"\s+", "", str(variables.get("region") or "")).lower()
        body = advise.run(purpose or "change", "", region, [], None, subscription)
        return [f for f in body["findings"] if f.get("level") == "warn"][:6]
    except Exception as error:  # noqa: BLE001 - advice is a courtesy; a draft must not depend on it
        log.info("azure change: advice not available (%s)", type(error).__name__)
        return []


def make_record(*, change_id: str, requester: str, template: str, variables: dict[str, Any], subscription: str, resource_group: str,
                purpose: str, answer: dict[str, Any], commit: str, advice: list[dict[str, Any]], agent: Any | None = None,
                planned_note: str = "", code: str | None = None) -> ChangeRecord:
    """The `azure_change` record for a plan the executor made and `check_plan_answer` accepted. Not saved. With `code` it is a
    free-code record: `playbook_name` `free`, the code as its `commands` lines, always `change-high`."""
    rail = rails(agent)
    free = code is not None
    entry = None if free else templates().get(template)
    template = "free" if free else template
    summary, cost = answer.get("summary") or {}, answer.get("cost") or {}
    level, reasons = tier.decide(entry, answer, rail, free_code=free)
    group_new = any(r.get("type") == "azurerm_resource_group" and r.get("action") == "create" for r in summary.get("resources") or [])
    checks = [{"label": str(c.get("label")), "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")} for c in answer.get("checks") or []]
    checks.append({"label": "Nothing is deleted, stopped or restarted", "ok": True, "detail": "every change in the plan is a create or an update"})
    monthly = cost.get("monthly_usd")
    checks.append({"label": "Cost within the ceiling for one change", "ok": True,
                   "detail": f"{'unknown' if monthly is None else monthly} USD a month against {rail['usd_month_per_change']}"})
    if planned_note:
        checks.append({"label": planned_note, "ok": True, "detail": ""})
    checks += [{"label": f"Advice: {f.get('check')}", "ok": True, "level": "warn", "detail": str(f.get("summary") or "")} for f in advice]
    now = time.time()
    return ChangeRecord(
        id=change_id,
        created_at=now,
        requested_by=requester,
        status=AWAITING_APPROVAL,
        kind="azure_change",
        playbook_name=template,
        commit=commit,
        variables=variables,
        limit=f"{subscription}/{resource_group}",
        summary=_one_line(purpose) or f"Add {'free code' if free else template} to {resource_group}",
        commands=code.split("\n") if free else describe_values(variables),
        diff=[str(line) for line in summary.get("lines") or []],
        render_method=FROM_REVIEWED_CLI,
        rendered_at=now,
        rollback_commands=[line for line in str(FREE_BACKOUT if free else (entry or {}).get("backout") or "").splitlines() if line.strip()],
        expires_at=now + valid_days(agent) * DAY,
        plan_digest=str(answer["plan_digest"]),
        tier=level,
        confirm="",
        preflight={
            "scope": {"subscription": subscription, "resource_group": resource_group, "region": str(variables.get("region") or ""),
                      "rg_new": group_new},
            "summary": summary, "cost": cost, "checks": checks, "advice": advice, "risk_reasons": reasons,
            "backout": FREE_BACKOUT if free else str((entry or {}).get("backout") or ""), "purpose": _one_line(purpose, 1000),
            "usd_month_per_change": rail["usd_month_per_change"], "drift": answer.get("drift") or [],
            "plan_digest": str(answer["plan_digest"]),
        },
    )


def differences(old: list[str], new: list[str]) -> str:
    """What a new plan has that the old one had not, and the reverse; for the agent to tell the engineer."""
    added, gone = [x for x in new if x not in old][:5], [x for x in old if x not in new][:5]
    return "\n".join(filter(None, [("Now planned, not before:\n  " + "\n  ".join(added)) if added else "",
                                   ("Planned before, not now:\n  " + "\n  ".join(gone)) if gone else ""])) or "The resource lines are the same; other detail differs."


def store_new(record: ChangeRecord, replaces: ChangeRecord | None = None) -> ChangeRecord:
    """Write the issued document, save the record (audited as drafted) and retire the one it replaces."""
    write_documents(record, "issued")
    change_store.save(record)
    audit_document(record, "issued")
    if replaces is not None and replaces.status == AWAITING_APPROVAL:
        replaces.status = SUPERSEDED
        change_store.save(replaces)
    return record


# --- after an apply -------------------------------------------------------------------------------------------


def _epoch(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def refresh_inventory(actor: str) -> tuple[str, float | None]:
    """Start an inventory pull and say what happened. `waive_gap` is passed when the pull supports it (build contract)."""
    try:
        from plugins._ea_azure.helpers import collect

        extra = {"waive_gap": True} if "waive_gap" in inspect.signature(collect.start).parameters else {}
        collect.start("change", actor, **extra)
        return "started", time.time()
    except Exception as error:  # noqa: BLE001 - Busy, TooSoon, NotConfigured: the apply result stands either way
        return f"not started ({_one_line(error, 120)})", None


def record_managed(record: ChangeRecord, native_ids: list[str]) -> int:
    """List what the product made, for drift and adoption. A failure is logged and does not lose the apply result."""
    try:
        with db.transaction(db.NO_SCOPE) as conn:
            for native in native_ids:
                conn.execute(
                    "INSERT INTO cloud_managed (native_id, workspace, change_id) VALUES (%s, %s, %s) "
                    "ON CONFLICT (native_id) DO UPDATE SET workspace = EXCLUDED.workspace, change_id = EXCLUDED.change_id, "
                    "applied_at = now(), removed_at = NULL",
                    (str(native).lower(), record.limit, record.id))
        return len(native_ids)
    except Exception as error:  # noqa: BLE001
        log.error("azure change: could not list managed resources (%s)", type(error).__name__)
        return 0


def finish(record: ChangeRecord, job: dict[str, Any], actor: str) -> str:
    """Settle a record from the executor's job answer: status, verify, what was made, the inventory, the applied document."""
    state = str(job.get("status") or "")
    changed = [str(x) for x in job.get("changed_ids") or []]
    record.status = {"applied": APPLIED, "verify_failed": VERIFY_FAILED, "failed": FAILED, "superseded": SUPERSEDED}[state]
    record.verified = job.get("verified") if state in ("applied", "verify_failed") else None
    record.error = redact.redact_text(str(job.get("error") or ""))[0][:500]
    if state != "superseded":
        record.applied_at = _epoch(job.get("ended_at")) or time.time()
    pulled, pulled_at = ("not needed", None) if state == "superseded" else refresh_inventory(actor)
    listed = record_managed(record, changed) if changed else 0
    change_store.save(record)  # the status line is audited here, before the document is written
    extra = {"started_at": _epoch(job.get("started_at")), "ended_at": _epoch(job.get("ended_at")), "verified": record.verified,
             "error": record.error, "changed_ids": changed, "inventory_pulled_at": pulled_at}
    note = ""
    if state != "superseded":
        try:
            write_documents(record, "applied", extra)
            change_store.save(record)
            audit_document(record, "applied")
        except Exception as error:  # noqa: BLE001 - the apply is done; say that the document was not
            log.error("azure change: applied document not written (%s)", type(error).__name__)
            note = "\nThe as-applied document could not be written; the record and the audit hold the result."
    lines = {"applied": "applied and verified", "verify_failed": "applied, but the verify did not find the intended state",
             "failed": "failed", "superseded": "not applied: the plan changed since it was approved, so nothing ran"}[state]
    made = ("\nCreated or changed:\n  " + "\n  ".join(changed[:20])) if changed else ""
    why = f"\nError: {record.error}" if record.error else ""
    return (f"Change `{record.id}` {lines}.{made}{why}\nInventory pull: {pulled}. Resources listed as made by the product: {listed}.{note}")


_inflight: set[str] = set()
_inflight_lock = threading.Lock()


@contextmanager
def in_flight(change_id: str) -> Iterator[bool]:
    """True for the one call in this process that is waiting on this change's apply. The settling loop (`helpers/settling.py`) leaves
    a change alone while one is, so the two never finish it twice; False means another call already is."""
    with _inflight_lock:
        mine = change_id not in _inflight
        _inflight.add(change_id)
    try:
        yield mine
    finally:
        if mine:
            with _inflight_lock:
                _inflight.discard(change_id)


def is_in_flight(change_id: str) -> bool:
    return change_id in _inflight


def settle(agent: Any | None, record: ChangeRecord, actor: str) -> str | None:
    """If an applying record's job has ended, finish it and return the message; None while it still runs."""
    if record.status != "applying" or not record.job_id:
        return None
    job = executor_client.job(agent, record.job_id)
    return finish(record, job, actor) if job.get("status") in executor_client.TERMINAL else None


# --- suggestions -------------------------------------------------------------------------------------------------


def runbook(kind: str) -> dict[str, Any]:
    try:
        entries = yaml.safe_load(RUNBOOKS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        entries = {}
    found = entries.get(kind) if isinstance(entries, dict) else None
    return found if isinstance(found, dict) and found.get("steps") else GENERIC_RUNBOOK


def close_record(record: ChangeRecord) -> None:
    record.status = CLOSED
    change_store.save(record)


def new_id() -> str:
    return uuid.uuid4().hex
