"""CEF formatting.

The format's two documented traps get their own tests, because both fail
silently at the collector rather than loudly here: header and extension escape
different characters, and the syslog priority byte is not the CEF severity.
"""

from __future__ import annotations

import pytest

from plugins._ea_siem_forward.helpers import cef

VENDOR = dict(device_vendor="Rezztor", device_product="EAgent", device_version="1.0")


def _record(**fields):
    base = {
        "v": 1,
        "ts": "2026-09-05T08:26:10.123+00:00",
        "event": "auth.signin",
        "actor": {"user_id": "u1", "username": "admin", "role": "admin"},
    }
    base.update(fields)
    return base


# --- header -----------------------------------------------------------------

def test_the_header_has_exactly_the_seven_documented_fields():
    line = cef.format_cef(_record(outcome="failure"), **VENDOR)
    header = line.split("|")
    assert header[0] == "CEF:0"
    assert header[1] == "Rezztor"
    assert header[2] == "EAgent"
    assert header[3] == "1.0"
    assert header[4] == "auth.signin", "Device Event Class ID reuses the audit event id"
    assert header[5] == "Auth signin"
    assert header[6] == "8", "a failed sign-in is high severity"


def test_a_pipe_in_a_header_field_is_escaped_but_an_equals_is_not():
    line = cef.format_cef(_record(event="weird|event=name"), **VENDOR)
    assert "weird\\|event=name" in line


def test_a_backslash_in_a_header_field_is_escaped():
    assert cef.escape_header("a\\b") == "a\\\\b"


def test_a_newline_never_survives_into_a_header_field():
    """A newline in the header would split one event into two syslog messages."""
    assert "\n" not in cef.escape_header("line one\nline two")


# --- extension --------------------------------------------------------------

def test_an_equals_in_an_extension_value_is_escaped_but_a_pipe_is_not():
    assert cef.escape_extension("a=b|c") == "a\\=b|c"


def test_a_newline_in_an_extension_becomes_the_literal_two_characters():
    assert cef.escape_extension("one\ntwo") == "one\\ntwo"
    assert cef.escape_extension("one\rtwo") == "one\\rtwo"


def test_the_actor_maps_onto_cefs_own_dictionary_keys():
    """Using CEF's standard keys means a SIEM parses these without per-customer
    field mapping."""
    line = cef.format_cef(_record(), **VENDOR)
    assert "suser=admin" in line
    assert "suid=u1" in line
    assert "spriv=admin" in line


def test_receipt_time_comes_from_the_event_not_from_now():
    """A queued event must keep the time it happened, not the time it was sent."""
    from datetime import datetime

    record = _record()
    expected = int(datetime.fromisoformat(record["ts"]).timestamp() * 1000)
    assert f"rt={expected}" in cef.format_cef(record, **VENDOR)


def test_receipt_time_is_absent_rather_than_wrong_for_an_unparseable_timestamp():
    line = cef.format_cef(_record(ts="not a timestamp"), **VENDOR)
    assert "rt=" not in line


def test_remaining_fields_pass_through_as_extension_pairs():
    line = cef.format_cef(_record(outcome="failure", tool="code_execution"), **VENDOR)
    assert "outcome=failure" in line
    assert "tool=code_execution" in line


def test_nested_values_are_left_out_rather_than_stringified():
    """CEF's extension is flat; a dict rendered into it would be unparseable
    noise, and the audit record already carries a digest and a capped preview."""
    line = cef.format_cef(_record(details={"a": 1}, items=[1, 2]), **VENDOR)
    assert "details=" not in line
    assert "items=" not in line


def test_the_bookkeeping_version_field_is_not_forwarded():
    assert "v=1" not in cef.format_cef(_record(), **VENDOR)


# --- severity ---------------------------------------------------------------

@pytest.mark.parametrize(
    "event,fields,expected",
    [
        ("auth.signin", {"outcome": "failure"}, 8),
        ("tool.start", {"outcome": "denied"}, 8),
        ("auth.signin", {"outcome": "success"}, 6),
        ("auth.signout", {}, 6),
        ("account.delete", {}, 5),
        ("settings.update", {}, 5),
        ("plugins.toggle", {}, 5),
        ("tool.start", {}, 3),
    ],
)
def test_severity_table(event, fields, expected):
    assert cef.severity(event, fields) == expected


def test_outcome_failure_outranks_the_events_own_category():
    """The reason severity is a function of both, not a per-event table."""
    assert cef.severity("tool.start", {}) == 3
    assert cef.severity("tool.start", {"outcome": "failure"}) == 8


# --- the whole line ---------------------------------------------------------

def test_the_rfc3164_line_matches_the_documented_shape():
    line = cef.format_line(
        _record(outcome="failure"),
        hostname="rezztor-eagent",
        facility=16,
        syslog_format="rfc3164",
        **VENDOR,
    )
    assert line.startswith("<134>Sep  5 08:26:10 rezztor-eagent CEF:0|Rezztor|EAgent|1.0|auth.signin|")


def test_the_priority_byte_is_facility_times_eight_plus_syslog_severity():
    """And is unrelated to the CEF severity field further along the line."""
    line = cef.format_line(
        _record(outcome="failure"), hostname="h", facility=16,
        syslog_format="rfc3164", **VENDOR,
    )
    assert line.startswith("<134>")          # 16*8 + 6
    assert "|8|" in line                      # CEF severity, a different scale

    local7 = cef.format_line(
        _record(), hostname="h", facility=23, syslog_format="rfc3164", **VENDOR,
    )
    assert local7.startswith("<190>")         # 23*8 + 6


def test_rfc5424_uses_the_structured_header_and_keeps_the_cef_payload():
    line = cef.format_line(
        _record(), hostname="h", facility=16, syslog_format="rfc5424", **VENDOR,
    )
    assert line.startswith("<134>1 2026-09-05T08:26:10.123+00:00 h EAgent - - - CEF:0|")


def test_a_line_is_a_single_line_whatever_the_input():
    """Anything that could introduce a newline has been escaped by the time the
    envelope is built -- otherwise one event would arrive as two."""
    line = cef.format_line(
        _record(event="a\nb", msg="one\ntwo\r\nthree"),
        hostname="h", facility=16, syslog_format="rfc3164", **VENDOR,
    )
    assert "\n" not in line and "\r" not in line
