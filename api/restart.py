import json
import threading

from helpers.api import ApiHandler, Request, Response

from helpers import process

# Long enough for the 202 to leave the socket before the server stops.
RESTART_DELAY_SECONDS = 1.0


class Restart(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        # Reloading here would stop the server (and in a container raise SystemExit)
        # inside the request, so the caller got a 500 for a restart that then happened.
        timer = threading.Timer(RESTART_DELAY_SECONDS, process.reload)
        timer.daemon = True
        timer.start()
        return Response(
            response=json.dumps({"ok": True, "restarting": True}),
            status=202,
            mimetype="application/json",
        )
