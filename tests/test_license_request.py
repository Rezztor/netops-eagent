"""The activation request code.

A deployment names itself in a string a person carries by email. Two properties
matter more than the rest, and most of these tests are about them:

- It survives transport. Email clients change case, wrap lines and re-flow
  dashes; people retype `O` for `0`. All of that has to decode.
- It does not survive corruption. A wrong character must fail loudly, because a
  code that decodes to the *wrong* instance id produces a licence that installs
  nowhere and explains nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rezlic.request_code import (
    MAX_NAME,
    PREFIX,
    ActivationRequest,
    RequestCodeError,
    build_request_code,
    parse_request_code,
)

INSTANCE = "8f2a19c47b3d4e1a9c02b7e155aa01cc"
ISSUED = datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc)


def code(**overrides) -> str:
    args = {
        "instance_id": INSTANCE,
        "deployment_name": "SOC-primary",
        "product_version": "2026.09.08",
        "issued_at": ISSUED,
    }
    args.update(overrides)
    return build_request_code(**args)


class TestRoundTrip:
    def test_everything_survives(self):
        parsed = parse_request_code(code())
        assert parsed == ActivationRequest(
            instance_id=INSTANCE,
            deployment_name="SOC-primary",
            product_version="2026.09.08",
            issued_at=ISSUED,
        )

    def test_it_is_prefixed_and_grouped(self):
        """So it reads as one thing to copy, not a wall of characters."""
        built = code()
        assert built.startswith(PREFIX)
        body = built[len(PREFIX) :]
        assert "-" in body
        assert all(len(group) <= 5 for group in body.split("-"))

    def test_it_is_shorter_than_the_json_it_replaces(self):
        """189 characters was the JSON+gzip measurement this format exists to
        beat. The number is not sacred; being well under it is."""
        assert len(code()) < 150

    def test_an_operator_set_instance_id_still_encodes(self):
        """The id is normally 32 hex characters and is packed as 16 bytes. A
        human can set it to anything, and that must not make activation
        impossible -- only slightly longer."""
        parsed = parse_request_code(code(instance_id="dr-site-manual"))
        assert parsed.instance_id == "dr-site-manual"

    def test_a_unicode_deployment_name_survives(self):
        assert parse_request_code(code(deployment_name="Zürich DR")).deployment_name == "Zürich DR"

    def test_a_long_name_is_truncated_not_refused(self):
        """A long label should shorten, never block activation."""
        parsed = parse_request_code(code(deployment_name="x" * 200))
        assert 0 < len(parsed.deployment_name) <= MAX_NAME


class TestTransportTolerance:
    @pytest.mark.parametrize(
        "mangle, label",
        [
            (lambda c: c.lower(), "lowercased by a mail client"),
            (lambda c: c.replace("-", ""), "dashes stripped"),
            (lambda c: c.replace(PREFIX, ""), "prefix dropped"),
            (lambda c: f"  \n {c}\n ", "surrounded by whitespace"),
            (lambda c: c.replace("-", "\n", 3), "line-wrapped"),
        ],
    )
    def test_it_still_decodes(self, mangle, label):
        assert parse_request_code(mangle(code())).instance_id == INSTANCE, label

    def test_confusable_characters_fold_back(self):
        """Crockford's alphabet has no O, I, L or U precisely because someone
        retyping from an email will write them for 0, 1, 1 and V."""
        built = code()
        assert parse_request_code(built.replace("0", "O")).instance_id == INSTANCE
        assert parse_request_code(built.replace("1", "I")).instance_id == INSTANCE


class TestCorruption:
    """The half that must never be lenient."""

    def test_a_single_wrong_character_is_refused(self):
        built = code()
        for position in (10, 25, 40, 60):
            broken = list(built)
            if broken[position] == "-":
                continue
            broken[position] = "Z" if broken[position] != "Z" else "2"
            with pytest.raises(RequestCodeError) as caught:
                parse_request_code("".join(broken))
            assert "did not survive" in caught.value.reason

    def test_truncation_is_refused(self):
        with pytest.raises(RequestCodeError):
            parse_request_code(code()[:-4])

    def test_two_swapped_characters_are_refused(self):
        built = list(code())
        built[20], built[21] = built[21], built[20]
        if "".join(built) != code():
            with pytest.raises(RequestCodeError):
                parse_request_code("".join(built))

    @pytest.mark.parametrize("junk", ["", "   ", "hello", PREFIX, "REZ-REQ-ZZ"])
    def test_junk_is_refused_with_a_readable_reason(self, junk):
        with pytest.raises(RequestCodeError) as caught:
            parse_request_code(junk)
        assert caught.value.reason and caught.value.reason[0].isupper() is False

    def test_a_character_outside_the_alphabet_is_named(self):
        with pytest.raises(RequestCodeError) as caught:
            parse_request_code(PREFIX + "!!!!!!!!!!!!!!!!!!!!!!!!")
        assert "not valid" in caught.value.reason

    def test_no_code_at_all(self):
        for value in (None, 123, [], {}):
            with pytest.raises(RequestCodeError):
                parse_request_code(value)  # type: ignore[arg-type]


class TestEncoding:
    def test_an_empty_instance_id_is_refused(self):
        """Better to say so than to emit a code naming nothing."""
        with pytest.raises(RequestCodeError):
            build_request_code(
                instance_id="", deployment_name="x", product_version="1", issued_at=ISSUED
            )

    def test_the_code_changes_when_the_deployment_does(self):
        assert code() != code(instance_id="a" * 32)
        assert code() != code(deployment_name="other")

    def test_age_is_reported_from_the_issue_time(self):
        parsed = parse_request_code(code())
        assert parsed.age_days(ISSUED + timedelta(days=9, hours=2)) == 9
        # Never negative, whatever the console's clock thinks.
        assert parsed.age_days(ISSUED - timedelta(days=3)) == 0
