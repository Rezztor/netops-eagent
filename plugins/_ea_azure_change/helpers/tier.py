"""The risk tier of an Azure change, decided by code from the plan and the template, never by the agent.

`change-high` for free code, more than `HIGH_RESOURCES` resources, a monthly cost over the guard rails'
`low_risk_usd_month`, a price that could not be found, or a template its reviewer did not mark `risk: low`. Everything
else is `change-low`. There is no `confirm` tier: a typed count is not asked for on an Azure card (D24).
"""

from __future__ import annotations

from typing import Any

HIGH_RESOURCES = 10
LOW, HIGH = "change-low", "change-high"


def decide(template: dict[str, Any] | None, plan: dict[str, Any], rails: dict[str, Any], free_code: bool = False) -> tuple[str, list[str]]:
    """`(tier, reasons)`; no reasons means low. `template` is the catalogue entry (None for free code or an unknown one)."""
    summary = plan.get("summary") or {}
    cost = plan.get("cost") or {}
    count = sum(int(summary.get(k) or 0) for k in ("add", "change", "import"))
    reasons: list[str] = []
    if free_code:
        reasons.append("free code, not a reviewed template")
    elif not template or template.get("risk") != "low":
        reasons.append("the template is not marked low risk by its reviewer")
    if count > HIGH_RESOURCES:
        reasons.append(f"{count} resources, more than {HIGH_RESOURCES}")
    if cost.get("unknown"):
        reasons.append("a price could not be found for " + ", ".join(str(a) for a in cost["unknown"][:5]))
    monthly = cost.get("monthly_usd")
    limit = rails.get("low_risk_usd_month")
    if monthly is not None and limit is not None and float(monthly) > float(limit):
        reasons.append(f"about {monthly} USD a month, over the {limit} USD low-risk limit")
    return (HIGH if reasons else LOW), reasons


def decide_set(entries: dict[str, dict[str, Any] | None], plan: dict[str, Any], rails: dict[str, Any]) -> tuple[str, list[str]]:
    """The tier of a change set: `decide` over the set's summed answer, high too when any step's template (`entries`, by name) is
    not marked low risk. Each template is named once."""
    _, reasons = decide({"risk": "low"}, plan, rails)
    named = [f"the template `{name}` is not marked low risk by its reviewer" for name, entry in sorted(entries.items())
             if not entry or entry.get("risk") != "low"]
    reasons = named + reasons
    return (HIGH if reasons else LOW), reasons
