"""Ask the user for a device credential through a secure form, never in chat.

The tool shows a card and ends the turn. The user types the secret into a
password input and the browser posts it straight to the network_inventory
endpoint (`cards.SECRET_ENDPOINT`, fixed in code), so the value never passes
through this tool, the chat or the model. The tool has no argument that carries
a secret and no action that reads one back; the executor has no route that does.
"""

from __future__ import annotations

from helpers.tool import Response, Tool

from plugins._ea_interaction.helpers import cards


class NetCredential(Tool):
    async def execute(self, **kwargs) -> Response:
        args = dict(self.args or {})
        action = str(args.get("action") or "request").strip().lower()
        if action != "request":
            return Response(message=f"Unknown action '{action}'. Use request.", break_loop=False)
        try:
            kvps = cards.show_secret_form(
                self.agent,
                args.get("name"),
                kind=args.get("kind") or "password",
                username=args.get("username") or "",
            )
        except cards.CardRejected as error:
            return Response(message=f"The credential form was not shown: {error}", break_loop=False)
        return Response(
            message=(
                f"Showed the secure form for credential '{kvps['credential_name']}'. Wait for "
                f"the user to answer \"Credential {kvps['credential_name']} stored.\"; do not "
                "ask for the value in chat."
            ),
            break_loop=True,
        )
