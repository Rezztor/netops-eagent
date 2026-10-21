from helpers.api import ApiHandler, Request, session


class ApiLogout(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        # Unauthenticated so it answers a session that is half gone, but the
        # token is still required: a sign-out is a state change, and without it
        # any page could sign a user out.
        return True

    async def process(self, input: dict, request: Request) -> dict:
        # Emitted before the session is cleared, while the actor is still
        # resolvable -- afterwards there is nobody to attribute it to.
        try:
            from plugins._audit.helpers import audit

            audit.emit("auth.signout")
        except Exception:
            pass

        # Every copy of the session ends, not only this browser's: a cookie
        # copied elsewhere stops matching the account's stamp.
        try:
            from helpers import login as _login, users

            user = _login.current_user()
            if user:
                users.end_sessions(user["id"])
        except Exception as exc:  # noqa: BLE001 -- this browser is still signed out below
            from helpers.print_style import PrintStyle

            PrintStyle.error(f"Sign-out could not end the account's other sessions: {exc}")

        try:
            session.clear()
        except Exception:
            from helpers import login as _login
            _login.logout_session()
            session.pop("csrf_token", None)
        return {"ok": True}
