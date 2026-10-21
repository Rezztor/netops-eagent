"""Azure change document: blocks from a change record, PDF and .docx bytes.

The product never deletes, stops or restarts anything in Azure. Part 9 is a
suggestion for the engineer; the CR/SR line in part 10 is left blank for
management. The plan digest is printed and embedded in the footer.

PDF: stdlib writer in ``pdf.py`` (D19: no PyMuPDF — AGPL unsuitable for new use).
DOCX: stdlib ``zipfile`` WordprocessingML in ``docx.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugins._ea_azure_change.helpers import docx as docx_mod
from plugins._ea_azure_change.helpers import pdf as pdf_mod

try:
    from plugins._ea_inventory.helpers import redact as _redact
except Exception:  # pragma: no cover - inventory plugin always present in product
    _redact = None

_STATUS = {
    "issued": "ISSUED FOR APPROVAL",
    "applied": "AS APPLIED",
    "runbook": "RUNBOOK",
}

_SENSITIVE = re.compile(r"(?i)\(sensitive\)|\[masked\]|\*+")


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _preflight(record: Any) -> dict[str, Any]:
    pf = _g(record, "preflight", {}) or {}
    return pf if isinstance(pf, dict) else {}


def _id8(record: Any) -> str:
    return str(_g(record, "id", "") or "")[:8]


def _plan_digest(record: Any) -> str:
    dig = str(_g(record, "plan_digest", "") or "")
    if dig:
        return dig
    return str((_preflight(record).get("plan_digest") or ""))


def _short_digest(digest: str) -> str:
    return (digest or "")[:16]


def _fmt_ts(ts: float | None, *, date_only: bool = False) -> str:
    if ts is None:
        return ""
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    if date_only:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _redact_text(text: str) -> str:
    if not text:
        return ""
    if _redact is not None:
        return _redact.redact_text(str(text))[0]
    return str(text)


def _mask(text: str) -> str:
    """A value the summary already masked stays as the mask token, never the text around it."""
    if _SENSITIVE.search(text):
        if "(sensitive)" in text.lower():
            return "(sensitive)"
        if "[masked]" in text.lower():
            return "[masked]"
    return text


def _compact(value: Any) -> str:
    """One line: JSON for a dict or list, masked as ``_mask`` says."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return _mask(json.dumps(value, sort_keys=True, default=str))
    return _mask(str(value))


def _cell(value: Any) -> str:
    """Table-cell text. A dict prints as ``key: value`` lines, one nested level indented; each value is masked on its own."""
    if not isinstance(value, dict):
        return _compact(value)
    lines: list[str] = []
    for key in sorted(value, key=str):
        inner = value[key]
        if isinstance(inner, dict) and inner:
            lines.append(f"{key}:")
            lines.extend(f"  {k}: {_compact(inner[k])}" for k in sorted(inner, key=str))
        else:
            lines.append(f"{key}: {_compact(inner)}")
    return "\n".join(lines)


def _usd(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


_GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def _sub_label(sub: str, name: str) -> str:
    """The subscription's name with its GUID in brackets; the GUID alone when no name is stored."""
    sub, name = str(sub or ""), str(name or "")
    if name and sub and name.lower() != sub.lower() and _GUID.match(sub):
        return f"{name} [{sub}]"
    return sub


def _scope(pf: dict[str, Any], record: Any) -> dict[str, Any]:
    scope = pf.get("scope") if isinstance(pf.get("scope"), dict) else {}
    # limit is "<subscription>/<resource group>" for Azure workspaces
    limit = str(_g(record, "limit", "") or "")
    sub, _, rg = limit.partition("/")
    return {
        "subscription": scope.get("subscription") or scope.get("subscription_name") or sub,
        "resource_group": scope.get("resource_group") or rg,
        "region": scope.get("region") or "",
        "organisation": scope.get("organisation") or scope.get("organization") or "",
        "rg_new": bool(scope.get("rg_new") or scope.get("resource_group_new")),
    }


def _resources(pf: dict[str, Any], record: Any) -> list[dict[str, Any]]:
    summary = pf.get("summary") if isinstance(pf.get("summary"), dict) else {}
    resources = summary.get("resources") if isinstance(summary.get("resources"), list) else []
    if resources:
        return [r for r in resources if isinstance(r, dict)]
    # Fall back to diff lines as single-column rows when summary is absent.
    out: list[dict[str, Any]] = []
    for line in _g(record, "diff", []) or []:
        out.append({"address": str(line), "type": "", "action": "", "name": "", "before": {}, "after": {}})
    return out


def _resource_table(resources: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[str]] = []
    for i, res in enumerate(resources, start=1):
        action = str(res.get("action") or "")
        name = str(res.get("name") or res.get("address") or "")
        rtype = str(res.get("type") or "")
        label = " ".join(p for p in (rtype, name) if p).strip() or str(res.get("address") or f"resource {i}")
        before = _cell(res.get("before") if res.get("before") not in (None, {}, []) else "none")
        after = _cell(res.get("after") if res.get("after") not in (None, {}, []) else "")
        rows.append([str(i), label, action, before, after])
    if not rows:
        rows = [["-", "(no resource changes in summary)", "", "", ""]]
    return {"type": "table", "headers": ["#", "Resource", "Action", "Before", "After"], "rows": rows, "widths": [1, 4, 2, 3, 6]}


def _cost(pf: dict[str, Any]) -> dict[str, Any]:
    cost = pf.get("cost") if isinstance(pf.get("cost"), dict) else {}
    return cost


def _counts(pf: dict[str, Any]) -> tuple[int, int, int, int]:
    summary = pf.get("summary") if isinstance(pf.get("summary"), dict) else {}
    return (
        int(summary.get("add") or 0),
        int(summary.get("change") or 0),
        int(summary.get("import") or 0),
        0,  # removes: always 0 — product never deletes
    )


def filename(record: Any, version: str, fmt: str, *, when: float | None = None) -> str:
    """Return the contract file name for one artefact (no directory)."""
    version = (version or "issued").lower()
    fmt = (fmt or "pdf").lower().lstrip(".")
    id8 = _id8(record) or "unknown"
    day = _fmt_ts(when if when is not None else _g(record, "created_at"), date_only=True) or "undated"
    if version == "runbook":
        return f"azure-runbook-{id8}-{day}.{fmt}"
    if version == "applied":
        ref = re.sub(r"[^A-Za-z0-9_-]+", "", str(_g(record, "reference", "") or "noref"))
        return f"azure-change-{id8}-applied-{ref}-{day}.{fmt}"
    return f"azure-change-{id8}-issued-{day}.{fmt}"


def _advice(pf: dict[str, Any]) -> list[dict[str, Any]]:
    advice = pf.get("advice") if isinstance(pf.get("advice"), list) else []
    if not advice:
        return [{"type": "para", "text": "No advice findings recorded on this draft."}]
    adv_rows = []
    for finding in advice:
        if not isinstance(finding, dict):
            continue
        adv_rows.append([
            str(finding.get("check") or finding.get("rule") or ""),
            str(finding.get("detail") or finding.get("evidence") or finding.get("summary") or ""),
            str(finding.get("as_of") or finding.get("inventory_date") or ""),
        ])
    return [{"type": "table", "headers": ["Check", "Evidence", "Inventory"], "rows": adv_rows or [["-", "-", "-"]], "widths": [2, 5, 2]}]


def _policy(checks: Any, none: str) -> list[dict[str, Any]]:
    checks = checks if isinstance(checks, list) else []
    if not checks:
        return [{"type": "para", "text": none}]
    merged: dict[str, list[str]] = {}  # one row per check: its worst result, its first detail
    rank = {"pass": 0, "warn": 1, "fail": 2}
    for chk in checks:
        if not isinstance(chk, dict):
            continue
        ok = chk.get("ok")
        result = "pass" if ok else ("warn" if chk.get("level") == "warn" else "fail")
        row = merged.setdefault(str(chk.get("label") or ""), [result, ""])
        row[0] = max(row[0], result, key=rank.get)
        row[1] = row[1] or str(chk.get("detail") or "")
    chk_rows = [[label, result, detail] for label, (result, detail) in merged.items()]
    return [{"type": "table", "headers": ["Check", "Result", "Detail"], "rows": chk_rows or [["-", "-", "-"]], "widths": [4, 1, 5]}]


def _approval(record: Any, version: str) -> list[dict[str, Any]]:
    ref_display = "" if version == "issued" else str(_g(record, "reference", "") or "")
    return [
        {
            "type": "table",
            "headers": ["CR / SR number", "Approved by (name, role)", "Signature and date"],
            "rows": [[ref_display or " ", " ", " "]],
            "widths": [2, 3, 3],
        },
        {
            "type": "para",
            "text": (
                "Enter the number on the approval card. The product runs only the plan with the digest above. "
                "If Azure changes first, this document is void and a new one is issued."
            ),
        },
    ]


def blocks(record: Any, version: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build document blocks from the change record only (plus applied ``extra``)."""
    version = (version or "issued").lower()
    extra = dict(extra or {})
    if _steps(record):
        return _set_blocks(record, version, extra)
    pf = _preflight(record)
    scope = _scope(pf, record)
    digest = _plan_digest(record)
    add_n, chg_n, imp_n, rem_n = _counts(pf)
    cost = _cost(pf)
    monthly = cost.get("monthly_usd")
    tier = str(_g(record, "tier", "") or "")
    reasons: list[str] = []
    for r in pf.get("risk_reasons") if isinstance(pf.get("risk_reasons"), list) else []:
        r = str(r).strip()
        if r and r.lower() != f"tier {tier}".lower() and r not in reasons:  # the tier is said once, not as a reason
            reasons.append(r)
    risk = f"{tier or '(unset)'}" + (": " + "; ".join(reasons) if reasons else "")
    requester = str(extra.get("requested_by_name") or _g(record, "requested_by", "") or "")
    approver = str(extra.get("approved_by_name") or _g(record, "approved_by", "") or "(unknown)")
    sub_label = _sub_label(scope.get("subscription"), extra.get("subscription_name"))

    status = _STATUS.get(version, version.upper())
    org = scope.get("organisation") or extra.get("organisation") or ""
    issued_at = _g(record, "created_at")
    expires_at = _g(record, "expires_at")
    id8 = _id8(record)
    title = str(_g(record, "summary", "") or "Azure change")
    rg_label = str(scope.get("resource_group") or "")
    if scope.get("rg_new") and rg_label and "(new)" not in rg_label:
        rg_label = f"{rg_label} (new)"

    out: list[dict[str, Any]] = []
    out.append({"type": "heading", "level": 1, "text": "Azure change request"})
    out.append({"type": "para", "text": title})
    out.append({"type": "para", "text": f"Status: {status}"})
    if org:
        out.append({"type": "para", "text": f"Organisation: {org}"})
    out.append({"type": "rule"})

    summary_rows = [
        ["Document", f"{id8}, issued {_fmt_ts(issued_at)}", "Valid until", _fmt_ts(expires_at, date_only=True)],
        ["Requested by", requester, "Risk", risk],
        [
            "Where",
            " · ".join(p for p in (sub_label, rg_label, str(scope.get("region") or "")) if p),
            "Cost",
            (f"About {_usd(monthly)} USD a month, list price" if monthly is not None else "unknown"),
        ],
        [
            "Effect",
            f"Adds {add_n}, changes {chg_n}, removes {rem_n}"
            + (f", imports {imp_n}" if imp_n else "")
            + ". Nothing is deleted, stopped or restarted.",
            "",
            "",
        ],
    ]
    out.append({"type": "table", "headers": [], "rows": summary_rows, "widths": [2, 5, 2, 4], "label_cols": [0, 2]})

    # 1 Purpose
    out.append({"type": "heading", "level": 2, "text": "1 Purpose"})
    purpose = _redact_text(str(extra.get("purpose") or pf.get("purpose") or title)).strip()
    purpose += "" if purpose.endswith((".", "!", "?")) else "."
    out.append({
        "type": "para",
        "text": f"{purpose} Written by the assistant from the request; read by the requester.",
    })

    # 2 Scope
    out.append({"type": "heading", "level": 2, "text": "2 Scope"})
    out.append({
        "type": "para",
        "text": (
            f"Subscription {sub_label or '(unset)'}; "
            f"resource group {rg_label or '(unset)'}; "
            f"region {scope.get('region') or '(unset)'}. "
            "Nothing is deleted, stopped or restarted. "
            "Any restart Azure itself would cause is refused before a document is written."
        ),
    })

    # 3 Resources
    out.append({"type": "heading", "level": 2, "text": "3 Resources"})
    out.append(_resource_table(_resources(pf, record)))

    # 4 Plan
    out.append({"type": "heading", "level": 2, "text": "4 Plan"})
    template = str(_g(record, "playbook_name", "") or "")
    commit = str(_g(record, "commit", "") or "")
    plan_lines = []
    summary = pf.get("summary") if isinstance(pf.get("summary"), dict) else {}
    for line in summary.get("lines") or _g(record, "diff", []) or []:
        plan_lines.append(str(line))
    if template and template not in ("free", "suggestion"):
        head = f"Template {template}" + (f" at commit {commit}" if commit else "") + "."
    elif template == "free":
        head = "Free code."
    else:
        head = f"Source: {template or '(none)'}."
    values = _g(record, "variables", {}) or {}
    value_lines = [f"{k} = {_compact(v)}" for k, v in sorted(values.items())] if isinstance(values, dict) else []
    mono = [head, *plan_lines, *value_lines, f"Plan digest (SHA-256):\n{digest}"]
    out.append({"type": "mono", "text": "\n".join(mono)})
    if template == "free":  # management approves this exact text: it is printed whole, never summarised
        code = _g(record, "commands", []) or []
        out.append({"type": "heading", "level": 3, "text": "The code, exactly as it will be planned and applied"})
        out.append({"type": "mono", "text": "\n".join(str(line) for line in code) or "(no code on the record)"})

    # 5 Cost
    out.append({"type": "heading", "level": 2, "text": "5 Cost"})
    items = cost.get("items") if isinstance(cost.get("items"), list) else []
    cost_rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cost_rows.append([
            str(item.get("address") or ""),
            str(item.get("meter") or ""),
            str(item.get("usd_month") if item.get("usd_month") is not None else ""),
        ])
    if cost_rows:
        out.append({"type": "table", "headers": ["Resource", "Meter", "USD / month"], "rows": cost_rows, "widths": [5, 3, 2]})
    priced_at = cost.get("priced_at") or ""
    ceiling = pf.get("usd_month_per_change") or extra.get("usd_month_per_change") or 250
    out.append({
        "type": "para",
        "text": (
            f"Monthly total about {_usd(monthly) if monthly is not None else 'unknown'} USD at list price"
            + (f" (prices as of {priced_at})" if priced_at else "")
            + f". Ceiling {ceiling} USD a month per change. "
            "List price, 730 hours, before data transfer and your discounts."
        ),
    })
    unknown = cost.get("unknown") if isinstance(cost.get("unknown"), list) else []
    if unknown:
        out.append({"type": "para", "text": "Unknown price: " + ", ".join(str(u) for u in unknown)})

    # 6 Risk
    out.append({"type": "heading", "level": 2, "text": "6 Risk"})
    out.append({
        "type": "para",
        "text": f"Tier {tier or '(unset)'}" + (f", because {'; '.join(reasons)}" if reasons else "") + ".",
    })

    # 7 Advice
    out.append({"type": "heading", "level": 2, "text": "7 Advice"})
    out.extend(_advice(pf))

    # 8 Policy
    out.append({"type": "heading", "level": 2, "text": "8 Policy"})
    out.extend(_policy(pf.get("checks"), "No policy checks recorded on this draft."))

    # 9 Back-out — suggestion only; never run by the product
    out.append({"type": "heading", "level": 2, "text": "9 Back-out and removal"})
    backout = str(
        pf.get("backout")
        or "\n".join(_g(record, "rollback_commands", []) or [])
        or "Remove by hand in the portal, after your own approval. The product will not delete, stop or restart anything."
    )
    out.append({
        "type": "para",
        "text": (
            "Steps for the engineer to take by hand, outside the product, after their own approval. "
            "Never run by the product."
        ),
    })
    for line in backout.splitlines():
        if line.strip():
            out.append({"type": "para", "text": line.strip()})

    # 10 Approval — CR/SR blank for management on issued; filled on applied from the record
    out.append({"type": "heading", "level": 2, "text": "10 Approval"})
    out.extend(_approval(record, version))

    # As applied extras
    if version == "applied":
        out.append({"type": "heading", "level": 2, "text": "11 As applied"})
        out.append({"type": "para", "text": f"Reference: {_g(record, 'reference', '') or '(none)'}."})
        out.append({
            "type": "para",
            "text": (
                f"Approved by {approver} "
                f"at {_fmt_ts(_g(record, 'approved_at'))}."
            ),
        })
        out.append({
            "type": "para",
            "text": (
                f"Run start {_fmt_ts(extra.get('started_at') or _g(record, 'applied_at'))}; "
                f"end {_fmt_ts(extra.get('ended_at'))}."
            ),
        })
        verified = extra.get("verified", _g(record, "verified"))
        out.append({"type": "para", "text": f"Verify result: {verified}."})
        if extra.get("inventory_pulled_at") is not None:
            out.append({"type": "para", "text": f"Inventory pull: {_fmt_ts(extra.get('inventory_pulled_at'))}."})
        err = str(extra.get("error") or _g(record, "error", "") or "")
        if err:
            out.append({"type": "para", "text": f"Error: {_redact_text(err)}"})
        native_rows = []
        for item in extra.get("resources") or extra.get("changed_ids") or []:
            if isinstance(item, dict):
                native_rows.append([str(item.get("address") or ""), str(item.get("native_id") or "")])
            else:
                native_rows.append(["", str(item)])
        if native_rows:
            out.append({
                "type": "table",
                "headers": ["Resource", "Azure ID"],
                "rows": native_rows, "widths": [4, 5],
            })

    return out


# --- a change set: one document for ordered steps (the plan's S3 and S4) ----------------------------------------------

#: What a step's section says about it once the set ran. A step that is only planned says nothing.
_STEP_RESULT = {
    "applied": "Applied and verified.",
    "applying": "Applying when this document was written.",
    "verify_failed": "Applied, but the verify did not find the intended state. It is in Azure.",
    "failed": "STOPPED: this step failed. The set stopped here.",
    "superseded": "STOPPED: the plan changed since it was approved, so nothing of this step ran. The set stopped here.",
    "not_run": "NOT RUN: an earlier step stopped the set.",
}
_IN_AZURE = ("applied", "verify_failed")  # what a step left in Azure, so what part 9 backs out


def _steps(record: Any) -> list[dict[str, Any]]:
    steps = _g(record, "steps", None)
    if str(_g(record, "playbook_name", "") or "") != "set" or not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


def _step_title(step: dict[str, Any]) -> str:
    return str(step.get("title") or f"{step.get('template') or 'step'} in {step.get('resource_group') or '(no group)'}")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _step_counts(step: dict[str, Any]) -> tuple[int, int]:
    summary = _dict(step.get("summary"))
    return int(summary.get("add") or 0), int(summary.get("change") or 0)


def _step_usd(step: dict[str, Any]) -> str:
    monthly = _dict(step.get("cost")).get("monthly_usd")
    return "unknown" if monthly is None else _usd(monthly)


def _set_status(version: str, steps: list[dict[str, Any]]) -> str:
    """The status line. An as-applied set says how far it got: all of it, part of it, or none."""
    if version != "applied":
        return _STATUS.get(version, version.upper())
    done = sum(1 for s in steps if s.get("status") == "applied")
    if done == len(steps):
        return _STATUS["applied"]
    return f"{_STATUS['applied']}, PARTIAL ({done} of {len(steps)})" if done else f"NOT APPLIED (0 of {len(steps)})"


def _set_groups(pf: dict[str, Any], record: Any, steps: list[dict[str, Any]]) -> list[str]:
    """Every resource group of the set, in step order, '(new)' for one the set makes."""
    listed = _dict(pf.get("scope")).get("resource_groups")
    if isinstance(listed, list) and listed:
        return [f"{g.get('name')}" + (" (new)" if g.get("new") else "") for g in listed if isinstance(g, dict) and g.get("name")]
    seen: list[str] = []
    for step in steps:
        group = str(step.get("resource_group") or "")
        if group and group not in seen:
            seen.append(group)
    return seen


def _step_section(record: Any, step: dict[str, Any], count: int, version: str, commit: str) -> list[dict[str, Any]]:
    """Resources, plan lines, values, the step's own digest and checks; once applied, what became of it."""
    n = step.get("n")
    out: list[dict[str, Any]] = [{"type": "heading", "level": 2, "text": f"3.{n} Step {n} of {count}: {_step_title(step)}"}]
    status = str(step.get("status") or "")
    if version == "applied" and status in _STEP_RESULT:
        line = _STEP_RESULT[status]
        times = [f"{k} {_fmt_ts(step.get(f))}" for k, f in (("started", "started_at"), ("ended", "ended_at")) if step.get(f)]
        if times:
            line += f" ({'; '.join(times)})"
        if status in _IN_AZURE:
            line += f" Verify result: {step.get('verified')}."
        out.append({"type": "para", "text": line})
        err = str(step.get("error") or "")
        if err:
            out.append({"type": "para", "text": f"Reason: {_redact_text(err)}"})
        ids = [str(i.get("native_id") or i.get("address") or "") if isinstance(i, dict) else str(i) for i in step.get("changed_ids") or []]
        if ids:
            out.append({"type": "table", "headers": ["Azure ID made or changed by this step"], "rows": [[i] for i in ids], "widths": [1]})
    summary = _dict(step.get("summary"))
    resources = summary.get("resources") if isinstance(summary.get("resources"), list) else []
    out.append(_resource_table([r for r in resources if isinstance(r, dict)]))
    values = step.get("variables")
    value_lines = [f"{k} = {_compact(v)}" for k, v in sorted(values.items())] if isinstance(values, dict) else []
    head = f"Template {step.get('template')}" + (f" at commit {commit}" if commit else "") + f", resource group {step.get('resource_group')}."
    mono = [head, *(str(x) for x in summary.get("lines") or []), *value_lines, f"Step plan digest (SHA-256):\n{step.get('plan_digest') or ''}"]
    out.append({"type": "mono", "text": "\n".join(mono)})
    out.extend(_policy(step.get("checks"), "No checks recorded for this step."))
    return out


def _set_backout(steps: list[dict[str, Any]], version: str) -> list[dict[str, Any]]:
    """Per step, last first. An as-applied document backs out only what is in Azure."""
    out: list[dict[str, Any]] = [{
        "type": "para",
        "text": (
            "Steps for the engineer to take by hand, outside the product, after their own approval. "
            "Never run by the product. The last step is backed out first."
        ),
    }]
    shown = [s for s in steps if version != "applied" or s.get("status") in _IN_AZURE]
    if version == "applied":
        if not shown:
            out.append({"type": "para", "text": "No step was applied, so there is nothing to back out."})
        for s in steps:
            if s.get("status") == "failed":  # a step that failed part-way may have made something the product never learned of
                out.append({"type": "para", "text": f"Step {s.get('n')} failed part-way: look in Azure for anything it made and back that out too."})
        out.append({"type": "para", "text": "Nothing was rolled back. The product never deletes, stops or restarts anything."})
    for s in reversed(shown):
        text = str(s.get("backout") or "") if not isinstance(s.get("backout"), list) else "\n".join(map(str, s["backout"]))
        out.append({"type": "para", "text": f"Step {s.get('n')}: {_step_title(s)}"})
        for line in (text or "Remove by hand in the portal, after your own approval.").splitlines():
            if line.strip():
                out.append({"type": "para", "text": line.strip()})
    return out


def _set_blocks(record: Any, version: str, extra: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocks of a change set: a steps table, one section per step, one digest, one approval block."""
    steps = _steps(record)
    n = len(steps)
    pf = _preflight(record)
    scope = _scope(pf, record)
    digest = _plan_digest(record)
    summary = _dict(pf.get("summary"))
    add_n = int(summary["add"]) if "add" in summary else sum(_step_counts(s)[0] for s in steps)
    chg_n = int(summary["change"]) if "change" in summary else sum(_step_counts(s)[1] for s in steps)
    cost = _cost(pf)
    monthly = cost.get("monthly_usd")
    tier = str(_g(record, "tier", "") or "")
    reasons = [r for r in dict.fromkeys(str(x).strip() for x in pf.get("risk_reasons") or []) if r and r.lower() != f"tier {tier}".lower()]
    risk = f"{tier or '(unset)'}" + (": " + "; ".join(reasons) if reasons else "")
    sub_label = _sub_label(scope.get("subscription"), extra.get("subscription_name"))
    groups = _set_groups(pf, record, steps)
    regions = _dict(pf.get("scope")).get("regions")
    where = " · ".join(p for p in (sub_label, ", ".join(groups), ", ".join(map(str, regions)) if isinstance(regions, list) else "") if p)
    org = scope.get("organisation") or extra.get("organisation") or ""
    title = str(_g(record, "summary", "") or "Azure change")
    requester = str(extra.get("requested_by_name") or _g(record, "requested_by", "") or "")
    approver = str(extra.get("approved_by_name") or _g(record, "approved_by", "") or "(unknown)")
    commit = str(_g(record, "commit", "") or "")

    out: list[dict[str, Any]] = [{"type": "heading", "level": 1, "text": "Azure change request"}, {"type": "para", "text": title},
                                 {"type": "para", "text": f"Status: {_set_status(version, steps)}"}]
    if org:
        out.append({"type": "para", "text": f"Organisation: {org}"})
    out.append({"type": "rule"})
    out.append({"type": "table", "headers": [], "widths": [2, 5, 2, 4], "label_cols": [0, 2], "rows": [
        ["Document", f"{_id8(record)}, issued {_fmt_ts(_g(record, 'created_at'))}", "Valid until", _fmt_ts(_g(record, "expires_at"), date_only=True)],
        ["Requested by", requester, "Risk", risk],
        ["Where", where, "Cost", f"About {_usd(monthly)} USD a month, list price" if monthly is not None else "unknown"],
        ["Effect", f"{n} steps, in order. Adds {add_n}, changes {chg_n}, removes 0. Nothing is deleted, stopped or restarted.", "", ""],
    ]})

    out.append({"type": "heading", "level": 2, "text": "1 Purpose"})
    purpose = _redact_text(str(extra.get("purpose") or pf.get("purpose") or title)).strip()
    purpose += "" if purpose.endswith((".", "!", "?")) else "."
    out.append({"type": "para", "text": f"{purpose} Written by the assistant from the request; read by the requester."})

    out.append({"type": "heading", "level": 2, "text": "2 Scope"})
    out.append({"type": "para", "text": (
        f"Subscription {sub_label or '(unset)'}; resource groups {', '.join(groups) or '(unset)'}. "
        f"One change set of {n} steps, applied in the order of part 3, approved once. Nothing is deleted, stopped or restarted. "
        "Any restart Azure itself would cause is refused before a document is written.")})

    out.append({"type": "heading", "level": 2, "text": "3 Steps"})
    out.append({"type": "table", "headers": ["#", "What", "Template", "Resource group", "Adds", "Changes", "USD / month"], "widths": [1, 5, 3, 3, 1.4, 1.8, 2.2], "rows": [
        [str(s.get("n")), _step_title(s), str(s.get("template") or ""), str(s.get("resource_group") or ""),
         str(_step_counts(s)[0]), str(_step_counts(s)[1]), _step_usd(s)] for s in steps]})
    for s in steps:
        out.extend(_step_section(record, s, n, version, commit))

    out.append({"type": "heading", "level": 2, "text": "4 Plan"})
    mono = [f"Change set of {n} steps" + (f" at commit {commit}" if commit else "") + ".", "Each step is planned on its own; its digest is in its section.",
            f"Set digest (SHA-256):\n{digest}"]
    continues = str(pf.get("continues") or "")
    if continues:
        mono.insert(1, f"Continues change {continues[:8]}: the steps it did not apply.")
    out.append({"type": "mono", "text": "\n".join(mono)})

    out.append({"type": "heading", "level": 2, "text": "5 Cost"})
    cost_rows: list[list[str]] = []
    for s in steps:
        items = [i for i in _dict(s.get("cost")).get("items") or [] if isinstance(i, dict)]
        label = f"{s.get('n')} {_step_title(s)}"
        for k, item in enumerate(items):
            cost_rows.append([label if not k else "", str(item.get("address") or ""), str(item.get("meter") or ""),
                              str(item.get("usd_month") if item.get("usd_month") is not None else "")])
        if not items:
            cost_rows.append([label, "(no priced resource)", "", _step_usd(s)])
    out.append({"type": "table", "headers": ["Step", "Resource", "Meter", "USD / month"], "rows": cost_rows, "widths": [4, 4, 2, 2]})
    priced_at = cost.get("priced_at") or ""
    ceiling = pf.get("usd_month_per_change") or extra.get("usd_month_per_change") or 250
    out.append({"type": "para", "text": (
        f"Monthly total of all {n} steps about {_usd(monthly) if monthly is not None else 'unknown'} USD at list price"
        + (f" (prices as of {priced_at})" if priced_at else "")
        + f". Ceiling {ceiling} USD a month for the whole set. List price, 730 hours, before data transfer and your discounts.")})
    if isinstance(cost.get("unknown"), list) and cost["unknown"]:
        out.append({"type": "para", "text": "Unknown price: " + ", ".join(str(u) for u in cost["unknown"])})

    out.append({"type": "heading", "level": 2, "text": "6 Risk"})
    out.append({"type": "para", "text": f"Tier {tier or '(unset)'}" + (f", because {'; '.join(reasons)}" if reasons else "") + " (one tier for the whole set)."})
    out.append({"type": "heading", "level": 2, "text": "7 Advice"})
    out.extend(_advice(pf))
    out.append({"type": "heading", "level": 2, "text": "8 Policy"})
    out.extend(_policy(pf.get("checks"), "No policy checks recorded on this draft."))
    out.append({"type": "heading", "level": 2, "text": "9 Back-out and removal"})
    out.extend(_set_backout(steps, version))
    out.append({"type": "heading", "level": 2, "text": "10 Approval"})
    out.extend(_approval(record, version))

    if version == "applied":
        done = [s for s in steps if s.get("status") == "applied"]
        stopped = next((s for s in steps if s.get("status") in ("failed", "verify_failed", "superseded")), None)
        out.append({"type": "heading", "level": 2, "text": "11 As applied"})
        out.append({"type": "para", "text": f"Reference: {_g(record, 'reference', '') or '(none)'}."})
        out.append({"type": "para", "text": f"Approved by {approver} at {_fmt_ts(_g(record, 'approved_at'))}."})
        said = f"Applied {len(done)} of {n} steps."
        if stopped:
            said += f" Stopped at step {stopped.get('n')}: {_redact_text(str(stopped.get('error') or stopped.get('status')))}."
            later = [str(s.get("n")) for s in steps if s.get("status") == "not_run"]
            said += f" Not run: {', '.join(later) if later else 'none'}. Steps that were applied stay in Azure; nothing is rolled back."
        out.append({"type": "para", "text": said})
        start = min((float(s["started_at"]) for s in steps if s.get("started_at")), default=extra.get("started_at"))
        end = max((float(s["ended_at"]) for s in steps if s.get("ended_at")), default=extra.get("ended_at"))
        out.append({"type": "para", "text": f"Run start {_fmt_ts(start or _g(record, 'applied_at'))}; end {_fmt_ts(end)}."})
        out.append({"type": "table", "headers": ["#", "Step", "Result", "Verified"], "widths": [1, 6, 4, 2], "rows": [
            [str(s.get("n")), _step_title(s), str(s.get("status") or "").replace("_", " "),
             str(s.get("verified")) if s.get("status") in _IN_AZURE else ""] for s in steps]})
        if extra.get("inventory_pulled_at") is not None:
            out.append({"type": "para", "text": f"Inventory pull: {_fmt_ts(extra.get('inventory_pulled_at'))}."})
        err = str(extra.get("error") or _g(record, "error", "") or "")
        if err:
            out.append({"type": "para", "text": f"Error: {_redact_text(err)}"})
    return out


def _footer_left(record: Any) -> str:
    id8 = _id8(record)
    short = _short_digest(_plan_digest(record))
    if _steps(record):  # the number once there is one, then the set digest
        return " · ".join(p for p in (id8, str(_g(record, "reference", "") or ""), f"set digest {short}") if p)
    return f"{id8} · digest {short}".strip(" ·")


def to_pdf(block_list: list[dict[str, Any]], *, footer_left: str = "", generated: str = "") -> bytes:
    """Render blocks to a PDF 1.4 byte string (stdlib Helvetica writer)."""
    return pdf_mod.render(block_list, footer_left=footer_left, generated=generated)


def to_docx(block_list: list[dict[str, Any]]) -> bytes:
    """Render blocks to a minimal .docx byte string (stdlib zipfile)."""
    return docx_mod.render(block_list)


def build(
    record: Any,
    version: str,
    extra: dict[str, Any] | None = None,
    *,
    generated_at: float | None = None,
) -> dict[str, bytes]:
    """Return ``{filename: bytes}`` for the formats this version writes.

    issued/applied → PDF and DOCX; runbook → PDF only.
    """
    version = (version or "issued").lower()
    when = generated_at if generated_at is not None else time.time()
    block_list = blocks(record, version, extra)
    gen = _fmt_ts(when)
    pdf_bytes = to_pdf(block_list, footer_left=_footer_left(record), generated=gen)
    out = {filename(record, version, "pdf", when=when): pdf_bytes}
    if version != "runbook":
        out[filename(record, version, "docx", when=when)] = to_docx(block_list)
    return out


def write_once(
    record: Any,
    version: str,
    dest_dir: str | Path,
    extra: dict[str, Any] | None = None,
    *,
    generated_at: float | None = None,
) -> list[dict[str, Any]]:
    """Write artefacts once under ``dest_dir``; return ``record.documents`` entries.

    Existing files are left untouched (never rewritten). SHA-256 is of the bytes
    that were written, or of the existing file when it was already present.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    when = generated_at if generated_at is not None else time.time()
    created = datetime.fromtimestamp(when, tz=timezone.utc).isoformat()
    metas: list[dict[str, Any]] = []
    for name, data in build(record, version, extra, generated_at=when).items():
        path = dest / name
        if path.exists():
            data = path.read_bytes()
        else:
            path.write_bytes(data)
        fmt = "docx" if name.endswith(".docx") else "pdf"
        metas.append({
            "version": version,
            "format": fmt,
            "sha256": hashlib.sha256(data).hexdigest(),
            "created_at": created,
            "downloaded_at": None,
            "filename": name,
        })
    return metas
