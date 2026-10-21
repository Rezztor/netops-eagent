"""Pause and ask the user to approve something before doing it.

This is the agent checking before it acts, not a permission system. Clicking the
card sends an ordinary message, so anyone who can hold the conversation can
answer it -- which is correct here, because they could have typed "yes" anyway.

**It cannot authorise anything.** An approval that carries authority -- approving
the commands a network change will push, say -- has to call an endpoint that
checks a permission, because a message cannot be told apart from someone typing
one. That is `cards.decision_card`, which the network change tool emits directly
rather than through a tool the model can aim: this one has no field through which
an endpoint could arrive, and that one takes its endpoint from an allowlist.
"""

from __future__ import annotations

from typing import Any

from helpers.parallel_tools import user_facing_context
from helpers.tool import Response, Tool

from plugins._ea_interaction.helpers import cards


class RequestApproval(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})

        try:
            kvps = cards.approval_card(
                args.get("prompt") or args.get("question"),
                consequence=str(args.get("consequence") or ""),
                approve_label=str(args.get("approve_label") or "Approve"),
                reject_label=str(args.get("reject_label") or "Reject"),
                allow_other=_flag(args.get("allow_other"), default=True),
            )
        except cards.CardRejected as error:
            return Response(
                message=f"That approval was not requested: {error}",
                break_loop=False,
            )

        surface = user_facing_context(self.agent)
        surface.log.log(
            type="info",
            heading=kvps["question"],
            content=kvps.get("consequence", ""),
            kvps=kvps,
        )

        return Response(
            message=(
                f"Asked the user to approve: {kvps['question']} "
                "Waiting for their decision; do not act until they answer, and do "
                "not treat silence as approval."
            ),
            break_loop=True,
        )


def _flag(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}
