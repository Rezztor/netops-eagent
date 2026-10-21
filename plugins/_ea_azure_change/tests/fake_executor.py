"""A stand-in for the cloud executor: replaces `executor_client._send`, so the real client code (paths, tokens, refusals)
runs and nothing leaves the process. It answers a plan, an apply, a job and the admin calls from what a test sets, and
keeps every call, so a test can say what did and did not reach the executor. It has no destroy and no delete."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cloud_executor.gate import set_digest
from plugins._ea_azure_change.helpers import executor_client

SUB = "11111111-2222-3333-4444-555555555555"
DIGEST_A, DIGEST_B = "a" * 64, "b" * 64
COMMIT = "c" * 40


def resource(address: str, action: str = "create", type_: str = "azurerm_virtual_network", name: str = "vnet-test") -> dict[str, Any]:
    return {"address": address, "type": type_, "action": action, "name": name, "before": {}, "after": {"location": "uaenorth"}}


def plan_answer(digest: str = DIGEST_A, lines: list[str] | None = None, resources: list[dict] | None = None, monthly: float | None = 0.0,
                checks: list[dict] | None = None) -> dict[str, Any]:
    resources = resources if resources is not None else [resource("azurerm_virtual_network.vnet")]
    return {
        "ok": True, "plan_digest": digest, "duration_s": 1,
        "summary": {"add": sum(r["action"] == "create" for r in resources), "change": sum(r["action"] == "update" for r in resources),
                    "import": 0, "lines": lines if lines is not None else [f"+ {r['address']}  {r['name']}" for r in resources], "resources": resources},
        "checks": checks if checks is not None else [{"label": "Static code check", "ok": True}, {"label": "Plan policy", "ok": True}],
        "cost": {"monthly_usd": monthly, "items": [], "unknown": [], "priced_at": "2026-10-03"}, "drift": [],
    }


def step_digest(step: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(step, sort_keys=True).encode()).hexdigest()


def plan_set_answer(subscription: str, steps: list[dict[str, Any]], monthly: float = 0.0, digests: list[str] | None = None) -> dict[str, Any]:
    """What the executor answers for a set: each step planned as one virtual network, a digest from the step itself (or `digests`)."""
    done = [{"n": n, "template": s["template"], "resource_group": s["resource_group"],
             **plan_answer(digests[n - 1] if digests else step_digest(s), resources=[resource(f"azurerm_virtual_network.s{n}", name=f"vnet-{n}")], monthly=monthly / len(steps))}
            for n, s in enumerate(steps, 1)]
    return {"ok": True, "set_digest": set_digest(subscription, done), "duration_s": 3,
            "steps": [{k: d[k] for k in ("n", "plan_digest", "summary", "checks", "cost", "drift")} for d in done],
            "cost": {"monthly_usd": monthly, "items": [], "unknown": [], "priced_at": "2026-10-03"},
            "checks": [{"label": "Cost of the set", "ok": True}, {"label": "Number of resources in the set", "ok": True}]}


class FakeExecutor:
    def __init__(self, monkeypatch: Any) -> None:
        self.calls: list[tuple[str, str, Any, str]] = []
        self.plan_answer: dict[str, Any] = plan_answer()
        self.apply_status = 202
        self.apply_error = "refused"
        self.job: dict[str, Any] = {"status": "applied", "verified": True, "changed_ids": ["/subscriptions/x/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test"],
                                    "started_at": 1759478300.0, "ended_at": 1759478400.0}
        self.admin: dict[tuple[str, str], Any] = {}
        #: A set: `plan_set` (the whole answer, or None to derive it from the steps asked), one job per step id (`jobs`, else `job`),
        #: and `busy` apply calls answered 429 before one is accepted.
        self.plan_set: dict[str, Any] | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.busy = 0
        #: `replies[path]` is a queue: each call to that path takes the next item, a `(status, body)` to answer with or None for the usual.
        self.replies: dict[str, list[tuple[int, Any] | None]] = {}
        monkeypatch.setattr(executor_client, "_send", self._send)
        monkeypatch.setattr(executor_client, "is_configured", lambda agent=None: True)
        monkeypatch.setattr(executor_client, "POLL_INTERVAL", 0.0)

    def _send(self, agent: Any, method: str, path: str, body: Any = None, *, key: str = "", timeout: float | None = None) -> tuple[int, Any]:
        self.calls.append((method, path, body, key))
        queue = self.replies.get(path)
        if queue and (scripted := queue.pop(0)) is not None:
            return scripted
        if path == "/healthz":
            return 200, {"ok": True, "templates_commit": COMMIT}
        if path == "/v1/plan":
            return 200, self.plan_answer
        if path == "/v1/plan-set":
            return 200, self.plan_set or plan_set_answer(body["subscription"], body["steps"])
        if path == "/v1/apply":
            if self.busy:
                self.busy -= 1
                return 429, {"error": "More than 6 requests in the last minute. Nothing was queued; try again shortly."}
            if self.apply_status != 202:
                return self.apply_status, {"error": self.apply_error}
            return 202, {"job_id": f"job-{body['change_id'].replace('.', '-')}" if "." in body["change_id"] else "job1"}
        if path.startswith("/v1/jobs/"):
            owner, _, n = path.removeprefix("/v1/jobs/").removeprefix("job-").rpartition("-")  # a step's job id is `job-<set id>-<nn>`
            return 200, self.jobs.get(f"{owner}.{n}", self.job)
        if (method, path) in self.admin:
            return 200, self.admin[(method, path)]
        return 404, {"error": "no such call"}

    def paths(self) -> list[str]:
        return [p for _m, p, _b, _k in self.calls]
