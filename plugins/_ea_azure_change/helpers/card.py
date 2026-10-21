"""The decision card of an Azure change and the question card of a suggestion, built from the record alone.

The card is display plus a call: what authorises is the `azure_approve` endpoint, which checks the hash, the CR/SR
number and the document again. The reference pattern on the card only tells the input when to enable Approve. Lists are
cut to fit the card's per-value budget and line width; the record keeps the full text, and the digest is what is approved.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from plugins._ea_ansible.helpers.change_model import ChangeRecord
from plugins._ea_azure_change.helpers import records, sets

APPROVE = "plugins/_ea_azure_change/azure_approve"
EDIT = "plugins/_ea_azure_change/azure_edit"
BUDGET = 3800  # under cards.VALUE_BUDGET, leaving room for the JSON around a list
LINE = 280
PRICE_NOTE = "list price, 730 hours, before data transfer and your discounts"


def _fit(lines: list[str], count: int) -> list[str]:
    out = [x if len(x) <= LINE else x[: LINE - 1] + "…" for x in (str(l) for l in lines[:count]) if x.strip()]
    while out and len(json.dumps(out, ensure_ascii=False)) > BUDGET:
        out.pop()
    if len(out) < len(lines):
        out.append(f"... {len(lines) - len(out)} more lines in the change document")
    return out


def _checks(record: ChangeRecord) -> list[dict[str, Any]]:
    """The card refuses a label over 60 characters; cut it here rather than lose the card."""
    return [{**{k: v for k, v in c.items() if k in ("ok", "detail", "level")}, "label": (str(c.get("label")) or "check")[:60]}
            for c in record.preflight.get("checks") or []][:20]


def _day(ts: float | None) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else ""


def _consequence(record: ChangeRecord) -> str:
    pf = record.preflight
    s, cost = pf.get("summary") or {}, pf.get("cost") or {}
    monthly = cost.get("monthly_usd")
    text = f"{len(record.steps)} steps. " if sets.is_set(record) else ""
    text += f"Adds {int(s.get('add') or 0)}, changes {int(s.get('change') or 0)}, removes 0"
    text += f", imports {int(s['import'])}." if s.get("import") else "."
    return text + (f" About {monthly} USD a month at list price." if monthly is not None else " The monthly cost is unknown.")


def _steps(record: ChangeRecord) -> list[dict[str, Any]]:
    """The set's steps for the card: `{n, title, adds, lines}`. The card cuts the plan lines to fit (and says how many); a title it
    cannot fit is refused, never cut, and `sets._title` keeps them short."""
    return [{"n": s["n"], "title": s["title"], "adds": int((s.get("summary") or {}).get("add") or 0),
             "lines": [str(x) for x in (s.get("summary") or {}).get("lines") or []]} for s in record.steps]


def _set_details(record: ChangeRecord) -> list[tuple[str, str]]:
    pf = record.preflight
    scope, cost = pf.get("scope") or {}, pf.get("cost") or {}
    monthly = cost.get("monthly_usd")
    rows = [
        ("Subscription", str(scope.get("subscription") or "")),
        ("Resource groups", ", ".join(f"{g['name']}{' (new)' if g.get('new') else ''}" for g in scope.get("resource_groups") or [])),
        ("Steps", f"{len(record.steps)}, in this order; each stops the ones after it if it does not apply"),
        ("Template commit", record.commit[:10]),
        ("Applies as", "the cloud executor, with the change application's own identity, never yours"),
        ("Monthly cost", ("unknown" if monthly is None else f"about {monthly} USD") + f" for the whole set ({PRICE_NOTE}; prices of {cost.get('priced_at') or 'an unknown date'})"),
    ]
    if pf.get("risk_reasons") and record.tier == "change-high":
        rows.append(("Why high risk", "; ".join(str(r) for r in pf["risk_reasons"])))
    if pf.get("continues"):
        rows.append(("Continues", f"change set {str(pf['continues'])[:8]}, which stopped part-way"))
    rows.append(("Change document", f"{record.id[:8]}, issued {_day(record.created_at)}, set digest {record.plan_digest[:16]}"))
    return [(k, v) for k, v in rows if v]


def _details(record: ChangeRecord) -> list[tuple[str, str]]:
    if sets.is_set(record):
        return _set_details(record)
    pf = record.preflight
    scope, cost = pf.get("scope") or {}, pf.get("cost") or {}
    items = [f"{i.get('address')}: {i.get('usd_month')}" for i in cost.get("items") or [] if isinstance(i, dict)]
    monthly = cost.get("monthly_usd")
    rows = [
        ("Subscription", str(scope.get("subscription") or "")),
        ("Resource group", str(scope.get("resource_group") or "") + (" (new)" if scope.get("rg_new") else "")),
        ("Region", str(scope.get("region") or "")),
        ("Template" if record.playbook_name != "free" else "Source",
         f"{record.playbook_name} ({record.commit[:10]})" if record.playbook_name != "free" else "Free code"),
        ("Applies as", "the cloud executor, with the change application's own identity, never yours"),
        ("Monthly cost", ("unknown" if monthly is None else f"about {monthly} USD") + f" ({PRICE_NOTE}; prices of {cost.get('priced_at') or 'an unknown date'})"),
    ]
    if items:
        rows.append(("Priced items", "; ".join(items)))
    if pf.get("risk_reasons") and record.tier == "change-high":
        rows.append(("Why high risk", "; ".join(str(r) for r in pf["risk_reasons"])))
    rows.append(("Change document", f"{record.id[:8]}, issued {_day(record.created_at)}, digest {record.plan_digest[:16]}"))
    return [(k, v) for k, v in rows if v]


def change_card_args(record: ChangeRecord) -> dict[str, Any]:
    """The keyword arguments for `cards.decision_card`, so a test can read them without a card."""
    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    free = record.playbook_name == "free"
    pattern = str(records.rails()["reference_pattern"])
    doc = lambda label, fmt: {"label": label, "payload": {"change_id": record.id, "version": "issued", "format": fmt}}  # noqa: E731
    extra = {"steps": _steps(record)} if sets.is_set(record) else {}
    return dict(
        **extra,
        prompt=record.summary or f"Apply {record.playbook_name} to {record.limit}?",
        endpoint=APPROVE,
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        consequence=_consequence(record),
        details=_details(record),
        commands=None if free else _fit(record.commands, 40),
        code=records.code_of(record) if free else None,
        checks=_checks(record),
        diff=record.diff,
        rollback=["Back-out: by hand, outside the product. The steps, last step first, are in the document, part 9."] if extra
        else ["Back-out: by hand, outside the product. The steps are in the document, part 9."],
        expires_at=record.expires_at,
        tier=record.tier,
        edit_endpoint=None if free or extra else EDIT,  # a free-code card or a set's has no Edit button: the agent drafts it again
        edit_payload={"change_id": record.id},
        reference={"label": "CR or SR number", "pattern": pattern, "example": records.example_for(pattern)},
        documents=[doc("Change document (PDF)", "pdf"), doc("Word copy (.docx)", "docx")],
    )


def change_card(record: ChangeRecord) -> dict[str, str]:
    from plugins._ea_interaction.helpers import cards

    return cards.decision_card(**change_card_args(record))


def suggestion_card_args(record: ChangeRecord) -> dict[str, Any]:
    return dict(
        question=f"Suggestion: {record.summary}"[:300],
        options=["I will do this myself, outside the product", "Not now"],
        details=[("Resources", ", ".join(record.limit.split(","))[:280]), ("Done by", "the engineer, by hand; the product never deletes, stops or restarts anything in Azure")],
        checks=_checks(record),
        commands=_fit(record.commands, 40),
        tier="suggestion",
        documents=[{"label": "Runbook (PDF)", "payload": {"change_id": record.id, "version": "runbook", "format": "pdf"}}],
    )


def suggestion_card(record: ChangeRecord) -> dict[str, str]:
    """A question card: its options send a chat message and authorise nothing; it never carries an endpoint."""
    from plugins._ea_interaction.helpers import cards

    return cards.question_card(**suggestion_card_args(record))


def show(agent: Any, record: ChangeRecord, kvps: dict[str, str]) -> None:
    """Put a card in the chat. Raises when it cannot; the caller says so, and `resume` draws a draft's card again."""
    from helpers.parallel_tools import user_facing_context

    user_facing_context(agent).log.log(type="info", heading=kvps["question"], content="\n".join(record.commands), kvps=kvps)
