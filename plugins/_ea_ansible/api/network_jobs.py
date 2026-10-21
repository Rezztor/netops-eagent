"""What ran, for the console to show.

Read-only, and gated on `network.view` rather than `plugins.manage`. An engineer
who needs to see what was pushed to a switch last Tuesday should not need the
permission that lets them point the agent at a different executor.

The job history is the executor's, not this instance's: the executor is the one
workload that talks to devices, and asking it is the only way to be sure the
answer is about the estate rather than about what this application remembers.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import executor_client


class NetworkJobs(ApiHandler):
    # `network.view`, granted in `helpers/api_policy.py` rather than declared
    # here. A handler's own required_permission() wins over the table, so
    # declaring it in both places would mean two answers to one question and a
    # table that quietly stops being the place to look.

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        job_id = str(input.get("job_id") or "").strip()
        try:
            if job_id:
                return {"ok": True, "job": executor_client.poll(None, job_id)}
            return {
                "ok": True,
                **executor_client.jobs(
                    None,
                    limit=int(input.get("limit") or 50),
                    offset=int(input.get("offset") or 0),
                    status=str(input.get("status") or ""),
                    source=str(input.get("source") or ""),
                    playbook=str(input.get("playbook") or ""),
                ),
            }
        except executor_client.ExecutorError as error:
            return {"ok": False, "error": str(error)}
