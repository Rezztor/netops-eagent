"""A 500 answers with the message, never with the traceback.

`format_error` renders the whole stack, and the generic exception arm of
`ApiHandler.handle_request` used to return that same string as the response
body. Two things made it worse than an untidy error page: the body is
`text/plain`, and `messageFor` in `frontend/src/api/client.ts` shows a plain
string body to the user verbatim -- so the trace was rendered on screen, not
merely present in the network tab. Any signed-in account could produce one by
sending a request a handler did not expect, including the `user` role, whose
whole permission set is `chat.use`.

The absence is what has to be tested. An endpoint that stops raising would make
a test that only reads the source pass while the leak returned through some
other handler, so this runs a handler that raises and reads the body it
produced.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.api import ApiHandler  # noqa: E402


class _RaisingHandler(ApiHandler):
    """Stands in for any handler that hits an unexpected condition."""

    async def process(self, input, request):
        raise Exception("No chats provided")


def _failed_response():
    from flask import request

    app = Flask("api-error-disclosure-test")
    handler = _RaisingHandler(app, threading.RLock())
    with app.test_request_context("/api/chat_load", method="POST", json={}):
        return asyncio.run(handler.handle_request(request=request))


def test_the_body_carries_the_message():
    """The message survives: this is not a swap for a blank 500, which would
    cost the frontend every actionable error it shows today."""
    response = _failed_response()
    assert response.status_code == 500
    assert response.get_data(as_text=True).strip() == "No chats provided"


def test_the_body_carries_no_traceback():
    body = _failed_response().get_data(as_text=True)
    for marker in ("Traceback", "File \"", "line ", "helpers/api.py", str(PROJECT_ROOT)):
        assert marker not in body, f"{marker!r} reached the client in a 500 body"
