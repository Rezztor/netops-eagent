from typing import Any

from helpers.extension import Extension


class CloseIdleSockets(Extension):
    def execute(self, data: dict[str, Any] | None = None, **kwargs):
        # A socket whose client has sent nothing for the session idle limit
        # is closed, as its HTTP session would have ended (helpers/ws.py).
        from helpers import ws, ws_manager

        sids = ws.idle_sids()
        if sids:
            ws_manager.get_shared_ws_manager().disconnect_sids((ws.NAMESPACE, sid) for sid in sids)
