"""Access logs record the endpoint, never the query string.

Uvicorn's access log is genuinely useful -- request rates, status codes, which
endpoint returned the 500 somebody is asking about -- and the audit trail does
not cover it, because it records decisions rather than traffic. It was off by
default, so a deployment had none of that.

Turning it on is only safe because of this filter. The query string is where
this application's sensitive material travels: `/reset?token=...` carries a
live, single-use password-reset token, and the file endpoints carry a user's own
paths. ADR 0005 puts stdout on the wire to a SIEM, so switching access logs on
without redaction would write credentials into the one log that is deliberately
kept and forwarded -- the same failure as printing a task token, reached by a
different route.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import server_startup  # noqa: E402


def access_record(request_line: str) -> logging.LogRecord:
    """An access record shaped the way uvicorn builds one."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", request_line, "1.1", 200),
        exc_info=None,
    )
    return record


def apply(request_line: str) -> str:
    server_startup._StripQueryStrings().filter(record := access_record(request_line))
    return record.args[2]  # type: ignore[index]


def test_a_password_reset_token_never_reaches_the_log():
    assert apply("/reset?token=live-single-use-secret") == "/reset?<redacted>"
    assert "live-single-use-secret" not in apply("/reset?token=live-single-use-secret")


def test_a_users_file_path_never_reaches_the_log():
    line = apply("/api/download_work_dir_file?path=notes/salary-review.txt")
    assert line == "/api/download_work_dir_file?<redacted>"
    assert "salary-review" not in line


def test_the_endpoint_itself_survives():
    """The whole point of the log. Redacting the path as well would leave a
    record that something happened and nothing about what."""
    assert apply("/api/message_async") == "/api/message_async"
    assert apply("/api/poll?x=1").startswith("/api/poll")


def test_the_filter_never_drops_a_record():
    """A logging filter returning False silently deletes the line. This one
    rewrites; it must not become a way to lose access logs entirely."""
    assert server_startup._StripQueryStrings().filter(access_record("/anything")) is True


def test_a_record_that_is_not_shaped_like_uvicorns_is_left_alone():
    record = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg="something else", args=None, exc_info=None,
    )
    assert server_startup._StripQueryStrings().filter(record) is True
    assert record.args is None


def test_installing_twice_adds_one_filter():
    logger = logging.getLogger("uvicorn.access")
    logger.filters = [f for f in logger.filters
                      if not isinstance(f, server_startup._StripQueryStrings)]

    server_startup.install_access_log_redaction()
    server_startup.install_access_log_redaction()

    installed = [f for f in logger.filters
                 if isinstance(f, server_startup._StripQueryStrings)]
    assert len(installed) == 1


def test_access_logs_are_on_by_default():
    """The setting the filter exists to make safe."""
    from helpers import settings

    assert settings.get_default_settings()["uvicorn_access_logs_enabled"] is True


def test_the_instance_token_in_an_mcp_or_a2a_path_never_reaches_the_log():
    """These two endpoints authenticate by a path segment, not a query string,
    so the query filter alone logged the token on every call."""
    assert apply("/mcp/t-live-instance-token/sse") == "/mcp/t-<redacted>/sse"
    assert apply("/mcp/t-live-instance-token/p-demo/messages/?session_id=1") == (
        "/mcp/t-<redacted>/p-demo/messages/?<redacted>"
    )
    assert apply("/a2a/t-live-instance-token/.well-known/agent.json") == (
        "/a2a/t-<redacted>/.well-known/agent.json"
    )
    assert apply("/app/assets/t-shirt.svg") == "/app/assets/t-shirt.svg"
