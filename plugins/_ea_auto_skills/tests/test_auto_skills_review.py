"""Reading the reviewer's reply.

Anything unusable is a skip. Missing a skill costs one opportunity; acting on a
half-parsed one writes an instruction file nobody reviewed.
"""

import pytest

from plugins._ea_auto_skills.helpers import review


def test_a_create_reply_parses():
    draft = review.parse_draft(
        '{"action": "create", "name": "rotate-keys", '
        '"description": "Rotate keys.", "body": "Steps.", "rationale": "Why."}'
    )
    assert draft.action == "create"
    assert draft.name == "rotate-keys"
    assert draft.body == "Steps."


def test_a_skip_reply_parses_without_the_other_fields():
    draft = review.parse_draft('{"action": "skip", "rationale": "Nothing reusable."}')
    assert draft.action == "skip"


def test_a_reply_wrapped_in_a_fence_parses():
    draft = review.parse_draft(
        '```json\n{"action": "skip", "rationale": "Nothing here."}\n```'
    )
    assert draft is not None
    assert draft.action == "skip"


def test_a_single_object_wrapped_in_an_array_parses():
    draft = review.parse_draft('[{"action": "skip", "rationale": "Nothing."}]')
    assert draft is not None
    assert draft.action == "skip"


def test_a_name_is_normalised_to_lower_case():
    draft = review.parse_draft(
        '{"action": "create", "name": "  Rotate-Keys  ", '
        '"description": "d", "body": "b", "rationale": "r"}'
    )
    assert draft.name == "rotate-keys"


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "I could not decide.",
        "{}",
        '{"action": "invent", "name": "x", "description": "d", "body": "b"}',
        '{"action": "create", "description": "d", "body": "b"}',
        '{"action": "create", "name": "x", "body": "b"}',
        '{"action": "create", "name": "x", "description": "d"}',
        '[{"action": "skip"}, {"action": "create"}]',
    ],
)
def test_an_unusable_reply_is_not_a_draft(reply):
    assert review.parse_draft(reply) is None
