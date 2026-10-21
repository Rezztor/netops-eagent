"""Ask the user a question they can answer with a click.

The tool ends the turn, exactly as `response` does. It does not block, hold a
thread, or wait on anything: the card renders, the turn is over, and the user's
answer arrives as an ordinary message whenever they give it. A question is a
turn that happens to end with buttons.

That matters more than it sounds. Blocking would mean a timeout, a held agent
loop and a model context kept open for however long the person takes to decide --
a resource leak wearing a UX costume, and one that fails worst exactly when the
question is hard.
"""

from __future__ import annotations

from typing import Any

from helpers.parallel_tools import user_facing_context
from helpers.tool import Response, Tool

from plugins._ea_interaction.helpers import cards


class AskUser(Tool):
    async def execute(self, **kwargs) -> Response:
        args: dict[str, Any] = dict(self.args or {})

        try:
            kvps = cards.question_card(
                args.get("question"),
                args.get("options"),
                allow_other=_flag(args.get("allow_other"), default=True),
                multi=_flag(args.get("multi"), default=False),
            )
        except cards.CardRejected as error:
            # Handed back as a tool result rather than raised: the agent can ask
            # a better question on the next step, and a refused card should not
            # end the turn with nothing on screen.
            return Response(
                message=f"That question was not asked: {error}",
                break_loop=False,
            )

        surface = user_facing_context(self.agent)
        surface.log.log(
            type="info",
            heading=kvps["question"],
            content=str(args.get("context") or ""),
            kvps=kvps,
        )

        # What the agent's own history records. It has to read as the question
        # having been put, or the next turn will ask it again.
        labels = ", ".join(option["label"] for option in cards.normalise_options(args.get("options")))
        return Response(
            message=(
                f"Asked the user: {kvps['question']} (options: {labels}). "
                "Waiting for their answer; do not continue until they reply."
            ),
            break_loop=True,
        )


def _flag(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}
