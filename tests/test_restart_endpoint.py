"""POST /api/restart answers before the restart takes effect, not with a 500 from the restart itself."""

import asyncio
import json
import types

from api import restart
from helpers import process


def test_restart_answers_202_and_reloads_only_after_the_response(monkeypatch):
    reloaded = []
    monkeypatch.setattr(restart, "RESTART_DELAY_SECONDS", 0.05)

    def reload():
        # What the container path does: raises SystemExit inside whatever called it.
        reloaded.append(True)

    monkeypatch.setattr(process, "reload", reload)
    handler = restart.Restart(app=None, thread_lock=None)
    response = asyncio.new_event_loop().run_until_complete(
        handler.handle_request(types.SimpleNamespace(is_json=False))
    )

    assert response.status_code == 202
    assert json.loads(response.get_data()) == {"ok": True, "restarting": True}
    assert not reloaded  # not inside the request
    import time

    time.sleep(0.3)
    assert reloaded


def test_container_exit_does_not_depend_on_the_calling_thread(monkeypatch):
    codes = []
    monkeypatch.setattr(process.os, "_exit", codes.append)
    monkeypatch.setattr(process, "_server", None)
    monkeypatch.setattr(process.runtime, "is_dockerized", lambda: True)
    process.reload()
    assert codes == [0]
