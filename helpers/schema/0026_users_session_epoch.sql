-- A sign-out that ends the session, not only the browser's copy of it.
--
-- A session is a signed cookie with no server state, so signing out removed the
-- cookie from the browser that asked and left every other copy working until it
-- expired -- a cookie lifted from a shared machine or a proxy log outlived the
-- sign-out by up to a day. Every session carries a stamp derived from this
-- number (helpers/login.py `_password_stamp`); sign-out bumps it, and every
-- session the account holds stops matching on its next request.
ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
