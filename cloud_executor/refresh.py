"""Refresh-only: what Azure changed outside the product, and accepting it into the state (ADR 0072).

Nothing here changes Azure. `plan` is `tofu plan -refresh-only`: it only reads, with the planner's key, and lists
`resource_drift`. `accept` plans the same way, checks the drift is exactly the digest the engineer approved, and applies
that saved refresh-only plan, which rewrites the state to match Azure and does nothing else. Neither can put anything back:
a drift is shown and accepted, never reverted, and there is no mode or argument that would.

The digest covers the drift (`mode: refresh_only`, the workspace, the source and every drifted resource's changed
attributes), so it is never equal to an apply's digest and an approval of one cannot be used for the other.

`runner.plan` already accepts `mode: refresh_only` but its digest covers *changes*, of which a refresh-only plan has none;
this module answers the same request with a digest over the drift. Wiring, in `app.py`:
`/v1/plan` calls `refresh.plan` when `mode` is `refresh_only`, and `POST /v1/refresh/accept` calls `refresh.accept`,
both inside `_one_at_a_time`.

# unverified: the shape of `resource_drift` is OpenTofu's documented JSON plan format (opentofu.org/docs/internals/json-format,
# "resource_drift": the same object as `resource_changes`); the fixture copies it. Not run against OpenTofu 1.13 yet.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from cloud_executor import gate, runner, store

MAX_ATTRS = 12


def _text(value: Any, limit: int = 70) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def drift_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every managed resource that differs from the state, sorted by address: `{address, type, native_id, action,
    changed: {path: [before, after]}}`. Values are masked the way a plan's are (`gate.changed_attributes`)."""
    items = []
    for rc in plan.get("resource_drift") or []:
        if not isinstance(rc, dict) or rc.get("mode", "managed") != "managed":
            continue
        change = rc.get("change") or {}
        native = next((str(side["id"]).lower() for side in (change.get("before"), change.get("after"))
                       if isinstance(side, dict) and isinstance(side.get("id"), str) and side["id"].startswith("/")), "")
        items.append({"address": rc.get("address"), "type": rc.get("type"), "native_id": native,
                      "action": gate.change_action(rc), "changed": gate.changed_attributes(rc)})
    return sorted(items, key=lambda i: str(i["address"]))


def lines_of(items: list[dict[str, Any]]) -> list[str]:
    """The card's lines: `~ address` with `-` / `+` for each attribute, `! address` for a resource gone from Azure."""
    lines: list[str] = []
    for item in items:
        if item["action"] == "delete":
            lines.append(f"! {item['address']}  no longer in Azure")
            continue
        lines.append(f"~ {item['address']}")
        for path, (before, after) in list(item["changed"].items())[:MAX_ATTRS]:
            lines += [f"    - {path}: {_text(before)}", f"    + {path}: {_text(after)}"]
        if len(item["changed"]) > MAX_ATTRS:
            lines.append(f"    ... {len(item['changed']) - MAX_ATTRS} more attributes")
    return lines


def digest(items: list[dict[str, Any]], source: dict[str, Any], workspace: Any) -> str:
    body = json.dumps({"mode": "refresh_only", "workspace": workspace, "source": source, "drift": items}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def judge(plan: dict[str, Any], src: runner.Source) -> dict[str, Any]:
    """The answer of a refresh-only plan, or `Refusal` when the plan would change anything but the state."""
    moving = sorted({str(gate.change_action(rc)) for rc in gate.managed_changes(plan)} - {"no-op", "read"})
    if moving:
        raise runner.Refusal("policy", f"A refresh-only plan must change nothing in Azure; this one has: {', '.join(moving)}.")
    items = drift_items(plan)
    workspace = {"subscription": src.draft["subscription"], "resource_group": src.draft["resource_group"]}
    return {
        "plan_digest": digest(items, src.digest_source, workspace),
        "summary": {"add": 0, "change": 0, "import": 0, "lines": [], "resources": []},
        "checks": [{"label": "Nothing in Azure changes", "ok": True, "detail": "a refresh-only plan only reads Azure"}],
        "drift": lines_of(items), "drifted": items,
    }


def plan(body: dict[str, Any], rails: dict[str, Any], catalogue: dict[str, Any], lookup: Any = None) -> dict[str, Any]:
    """`POST /v1/plan` with `mode: refresh_only`. Same signature as `runner.plan`; a refusal is an answer, not an exception."""
    started = time.monotonic()
    try:
        if body.get("mode") != "refresh_only":
            raise runner.Refusal("gate", "This plans a refresh only.")
        sub, group = runner.workspace_of(body)
        src = runner.resolve(body.get("source"), sub, group, rails, catalogue)
        with runner.workdir(src) as work:
            env, secrets = runner.environment("planner", work)
            result = judge(runner.plan_in(work, src, "refresh_only", env, secrets), src)
    except runner.Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    return {"ok": True, **result, "duration_s": round(time.monotonic() - started, 1)}


def accept(body: dict[str, Any], rails: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, Any]:
    """`POST /v1/refresh/accept`: plan again, and only if the drift is the approved digest, write it into the state.

    Needs no reference: nothing in Azure changes. It runs as the planner (Reader), which cannot change Azure even if it
    wanted to. The state is copied into `cloud_state_version` first, and a change id is accepted once.
    """
    try:
        sub, group = runner.workspace_of(body)
        src = runner.resolve(body.get("source"), sub, group, rails, catalogue)
        with runner.workdir(src) as work:
            env, secrets = runner.environment("planner", work)
            result = judge(runner.plan_in(work, src, "refresh_only", env, secrets, keep=True), src)
            if result["plan_digest"] != body.get("plan_digest"):
                return {"ok": False, "stage": "policy", "superseded": True,
                        "refusal": "Azure is not as it was when the drift was shown, so nothing was accepted. Ask again."}
            try:
                store.record_apply(workspace=src.state_key, state_name=src.workspace_name, change_id=body["change_id"],
                                   code=src.code, variables=src.variables, plan_digest=body["plan_digest"], reference="")
            except store.AlreadyApplied as error:
                return {"ok": False, "stage": "policy", "refusal": str(error)}
            code, _out, err = runner.tofu(["apply", "-input=false", "-no-color", "-lock-timeout=60s", "plan.bin"], work, env, secrets)
            if code:
                return {"ok": False, "stage": "plan", "refusal": f"accepting the state failed: {err}"}
    except runner.Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    return {"ok": True, "plan_digest": result["plan_digest"], "accepted": [i["native_id"] for i in result["drifted"] if i["native_id"]]}
