"""The static code check and the plan policy, imported from the cloud executor's own file.

`cloud_executor/gate.py` is the one definition (build contract, "The static gate and the plan policy"); the application
imports it for early feedback and the executor enforces it. Fails closed: if the file is not in this image, every
check refuses, so a missing gate can never read as a passing one.
"""

from __future__ import annotations

try:
    from cloud_executor.gate import check_code, check_plan  # noqa: F401
except ImportError:  # pragma: no cover - exercised by a test that blocks the import

    def check_code(files: dict[str, str]) -> list[str]:
        return ["The static code check is not installed in this image, so code cannot be checked and is refused."]

    def check_plan(plan: dict, rails: dict, draft: dict, inventory_ids: set[str]) -> tuple[list[dict], list[str]]:
        return [], ["The plan policy is not installed in this image, so no plan can be checked and every plan is refused."]
