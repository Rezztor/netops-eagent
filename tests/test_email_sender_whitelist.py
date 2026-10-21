"""Who may reach the agent by email.

The guard used to read `if whitelist and not _matches_whitelist(...)`, so an
unset whitelist accepted every address on the internet -- and the shipped
example config demonstrated exactly that setting. These tests hold the
inverted default in place.
"""

from __future__ import annotations

import pytest

from plugins._email_integration.helpers.imap_client import (
    _extract_email_from_sender,
    _sender_allowed,
)


@pytest.mark.parametrize("whitelist", [None, [], ()])
def test_an_unset_whitelist_accepts_nothing(whitelist):
    """The point of the change. Absence of configuration is not consent.

    `None` is passed as itself, not coerced to a list: it is the unset case, and
    the type guard added for string whitelists must not mistake it for one.
    """
    assert _sender_allowed("anyone@example.com", whitelist) is False


def test_accepting_anyone_has_to_be_said_out_loud():
    assert _sender_allowed("anyone@example.com", ["*"]) is True


def test_a_domain_pattern_admits_only_that_domain():
    assert _sender_allowed("colleague@rezztor.com", ["*@rezztor.com"]) is True
    assert _sender_allowed("stranger@elsewhere.com", ["*@rezztor.com"]) is False


def test_one_named_sender():
    assert _sender_allowed("boss@rezztor.com", ["boss@rezztor.com"]) is True
    assert _sender_allowed("notboss@rezztor.com", ["boss@rezztor.com"]) is False


def test_the_display_name_cannot_smuggle_an_address_past_the_list():
    """A forged display name naming an allowed sender does not get in on the
    real one's behalf.

    The quoted form parses, and the address is the bracketed one. The unquoted
    form is not a valid header at all, so it resolves to nothing and is refused
    -- stricter than the hand-rolled parser this replaced, and the right way
    round.
    """
    quoted = '"Boss boss@rezztor.com" <attacker@elsewhere.com>'
    assert _extract_email_from_sender(quoted) == "attacker@elsewhere.com"
    assert _sender_allowed(quoted, ["boss@rezztor.com"]) is False

    unquoted = 'Boss boss@rezztor.com <attacker@elsewhere.com>'
    assert _extract_email_from_sender(unquoted) == ""
    assert _sender_allowed(unquoted, ["boss@rezztor.com"]) is False


@pytest.mark.parametrize(
    "forged",
    [
        "boss@rezztor.com <attacker@evilhost>",          # no dot in the domain
        "boss@rezztor.com <root@localhost>",             # no dot in the domain
        'boss@rezztor.com <"a b"@elsewhere.com>',        # space from a quoted local part
    ],
)
def test_an_unparseable_bracket_does_not_promote_the_display_name(forged):
    """The regression that reopened the fail-open, one layer down.

    The old parser treated the angle brackets as authoritative *only if* their
    contents passed a validation regex. Each address here fails it, and the
    parser then scanned the whole header and returned `boss@rezztor.com` -- the
    address the attacker typed into the display name -- which the whitelist duly
    admitted. A well-formed hostile address was still rejected, which is exactly
    why the suite stayed green.
    """
    assert _sender_allowed(forged, ["boss@rezztor.com"]) is False


def test_a_sender_that_parses_to_nothing_is_refused_even_by_a_wildcard():
    """"Accept anyone" means any address, not any bytes."""
    assert _sender_allowed("not an address at all <>", ["*"]) is False


@pytest.mark.parametrize("whitelist", ["*@rezztor.com", "*", "boss@rezztor.com"])
def test_a_whitelist_written_as_a_string_accepts_nobody(whitelist):
    """One missing pair of brackets.

    A string is truthy, so it passes the empty-whitelist guard, and the matcher
    then iterates it one character at a time -- where `*` is a pattern matching
    every address on the internet. The shipped example config documents the list
    form, so this is the single most likely way to write it wrong.
    """
    assert _sender_allowed("attacker@evil.com", whitelist) is False


def test_matching_ignores_case():
    assert _sender_allowed("Boss@Rezztor.com", ["boss@rezztor.com"]) is True
