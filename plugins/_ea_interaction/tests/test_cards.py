"""What a question card refuses, which is most of its value.

A card the user cannot act on is worse than prose: it looks answerable. So the
validation is the feature, and these are the shapes it turns down.
"""

from __future__ import annotations

import json

import pytest

from plugins._ea_interaction.helpers import cards


def test_a_plain_list_of_strings_is_enough():
    card = cards.question_card("Which switches?", ["Core", "Access"])
    assert card["interaction"] == "question"
    assert card["question"] == "Which switches?"
    assert json.loads(card["options"]) == [{"label": "Core"}, {"label": "Access"}]
    assert card["allow_other"] == "true"
    assert card["interaction_id"].startswith("q_")


def test_details_survive_and_are_optional():
    card = cards.question_card(
        "Which switches?",
        [{"label": "Core", "detail": "core-sw-1, core-sw-2"}, "Access"],
    )
    assert json.loads(card["options"]) == [
        {"label": "Core", "detail": "core-sw-1, core-sw-2"},
        {"label": "Access"},
    ]


def test_one_option_is_a_statement_not_a_question():
    with pytest.raises(cards.CardRejected, match="between 2 and 4"):
        cards.question_card("Proceed?", ["Yes"])


def test_five_options_is_a_menu():
    with pytest.raises(cards.CardRejected, match="between 2 and 4"):
        cards.question_card("Which?", ["a", "b", "c", "d", "e"])


def test_duplicate_labels_are_refused_because_the_answer_would_be_ambiguous():
    with pytest.raises(cards.CardRejected, match="same label"):
        cards.question_card("Which?", ["Core", "core"])


def test_an_empty_question_is_refused():
    with pytest.raises(cards.CardRejected, match="question"):
        cards.question_card("   ", ["a", "b"])


def test_an_empty_option_label_is_refused():
    with pytest.raises(cards.CardRejected, match="option"):
        cards.question_card("Which?", ["Core", "  "])


def test_options_that_would_be_truncated_on_the_wire_are_refused():
    """helpers/log.py caps a kvps value, and a truncated JSON list renders as a
    broken card rather than as an error."""
    long_detail = "x" * (cards.DETAIL_MAX)
    with pytest.raises(cards.CardRejected):
        cards.question_card(
            "Which?",
            [{"label": f"opt{i}", "detail": long_detail} for i in range(4)]
            + [{"label": "extra"}],
        )


def test_options_must_be_a_list():
    with pytest.raises(cards.CardRejected, match="must be a list"):
        cards.question_card("Which?", "Core or Access")


def test_flags_are_carried_as_strings():
    card = cards.question_card("Which?", ["a", "b"], allow_other=False, multi=True)
    assert card["allow_other"] == "false"
    assert card["multi"] == "true"


# --- approvals ---------------------------------------------------------------

def test_an_approval_offers_two_toned_decisions():
    card = cards.approval_card("Restart the interface on core-sw-1?")
    assert card["interaction"] == "approval"
    assert card["interaction_id"].startswith("a_")
    assert json.loads(card["options"]) == [
        {"label": "Approve", "tone": "go"},
        {"label": "Reject", "tone": "stop"},
    ]
    assert card["multi"] == "false"


def test_the_consequence_is_carried_when_given():
    card = cards.approval_card("Restart it?", consequence="The port drops for ~10s.")
    assert card["consequence"] == "The port drops for ~10s."
    assert "consequence" not in cards.approval_card("Restart it?")


def test_the_decision_labels_can_be_reworded():
    card = cards.approval_card("Proceed?", approve_label="Go ahead", reject_label="Stop")
    options = json.loads(card["options"])
    assert [option["label"] for option in options] == ["Go ahead", "Stop"]
    # The tone is what the interface colours by, so rewording cannot break it.
    assert [option["tone"] for option in options] == ["go", "stop"]


def test_an_approval_card_carries_no_authority():
    """A card cannot authorise anything, and this is where that stays true.

    Clicking sends an ordinary message, which the server cannot tell apart from
    the user typing the same word. If a field ever appears here through which an
    endpoint could arrive, approving a production change becomes something anyone
    with chat access can do -- and nothing would visibly break.

    Changing this test should be a deliberate act, not a consequence.
    """
    card = cards.approval_card("Approve these 4 commands?")
    forbidden = {"endpoint", "payload", "url", "action", "permission", "change_id"}
    assert not (forbidden & set(card)), f"authority leaked into a message-backed card: {card}"


def test_an_empty_prompt_is_refused():
    with pytest.raises(cards.CardRejected, match="prompt"):
        cards.approval_card("  ")


def test_an_unknown_tone_is_refused():
    with pytest.raises(cards.CardRejected, match="decision tone"):
        cards.question_card("Which?", [{"label": "a", "tone": "maybe"}, "b"])


# --- the card that does carry authority --------------------------------------
#
# `approval_card` and `decision_card` sit either side of the only distinction
# that matters here, so they are tested against each other rather than apart.
# An approval is the agent checking before doing something it may already do,
# and a click on it is a chat message. A decision is the decision: an engineer
# approving the exact commands a change will push, which cannot ride on a
# message because a message proves nothing about who sent it.

ANSIBLE_APPROVE = "plugins/_ea_ansible/change_approve"


def _decision(**overrides):
    kwargs = {
        "endpoint": ANSIBLE_APPROVE,
        "approve_payload": {"change_id": "c1", "decision": "approve"},
        "reject_payload": {"change_id": "c1", "decision": "reject"},
    }
    kwargs.update(overrides)
    return cards.decision_card("Approve these 4 commands?", **kwargs)


def test_a_decision_card_names_the_endpoint_that_will_check_the_permission():
    card = _decision()
    assert card["interaction"] == cards.KIND_DECISION
    assert card["endpoint"] == ANSIBLE_APPROVE


def test_a_decision_card_cannot_name_an_endpoint_that_is_not_on_the_list():
    """The agent builds this card. An endpoint it could fill freely would be a
    button the agent aims and the viewer fires -- the agent choosing the target
    and the user unknowingly supplying the authority."""
    for endpoint in ("users_create", "settings_set", "plugins", "../users_create", ""):
        with pytest.raises(cards.CardRejected):
            _decision(endpoint=endpoint)


def test_the_allowlist_is_short_and_deliberate():
    """Growing this list is a security decision, not a refactor. The test exists
    so that adding a name to it cannot happen quietly."""
    assert cards.AUTHORISED_ENDPOINTS == frozenset(
        {ANSIBLE_APPROVE, "plugins/_ea_azure/azure_read_approve", "plugins/_ea_azure_change/azure_approve"}
    )
    assert cards.DOCUMENT_ENDPOINTS == frozenset({"plugins/_ea_azure_change/azure_document"})


def test_a_card_may_offer_an_edit_only_through_the_listed_endpoint():
    card = _decision(edit_endpoint="plugins/_ea_ansible/read_edit", edit_payload={"change_id": "c1"})
    assert (card["editable"], card["edit_endpoint"]) == ("true", "plugins/_ea_ansible/read_edit")
    assert json.loads(card["edit_payload"]) == {"change_id": "c1"}
    assert "editable" not in _decision()
    with pytest.raises(cards.CardRejected):
        _decision(edit_endpoint=ANSIBLE_APPROVE)


def test_a_decision_carries_one_payload_per_option():
    card = _decision()
    payloads = json.loads(card["payloads"])
    options = json.loads(card["options"])
    assert len(payloads) == len(options) == 2
    assert payloads[0]["decision"] == "approve"
    assert payloads[1]["decision"] == "reject"


def test_a_decision_does_not_offer_something_else():
    """"Something else" is a reply, and an endpoint has nothing to do with a
    sentence. Offering it would produce a third button that looks like a third
    decision and is not one."""
    assert _decision()["allow_other"] == "false"


def test_a_decision_is_never_a_multi_select():
    assert _decision()["multi"] == "false"


def test_an_approval_card_and_a_decision_card_are_not_the_same_kind():
    """They render alike and mean different things. If the interface could not
    tell them apart, a button that records an approval and a button that sends
    the word "Approve" would be indistinguishable on screen."""
    assert cards.approval_card("x")["interaction"] != _decision()["interaction"]


def test_a_payload_too_large_to_travel_is_refused():
    with pytest.raises(cards.CardRejected, match="too long"):
        _decision(approve_payload={"blob": "x" * (cards.VALUE_BUDGET + 1)})


def test_a_decision_card_carries_its_evidence_as_strings():
    card = _decision(
        details=[("Device", "lab-sonic"), ("Address", "172.30.30.11")],
        commands=["show interfaces status"],
        checks=[{"label": "Reachable", "ok": True}, {"label": "Credential", "ok": False, "detail": "none"}],
        expires_at=1000.5,
        tier="read",
    )
    assert json.loads(card["details"])[0] == {"label": "Device", "value": "lab-sonic"}
    assert json.loads(card["commands"]) == ["show interfaces status"]
    assert json.loads(card["checks"])[1] == {"label": "Credential", "ok": False, "detail": "none"}
    assert card["expires_at"] == "1000.5" and card["tier"] == "read"
    assert all(isinstance(v, str) for v in card.values())
    assert {"details", "commands", "checks", "expires_at", "tier"}.isdisjoint(_decision())


def test_a_decision_card_refuses_malformed_evidence():
    for bad in (
        {"details": [("only one",)]},
        {"commands": [""]},
        {"commands": ["x" * 400]},
        {"checks": ["not an object"]},
        {"expires_at": "soon"},
        {"tier": "write"},
        {"commands": ["show interfaces status"] * 41},
        {"commands": ["x" * 290] * 20},
    ):
        with pytest.raises(cards.CardRejected):
            _decision(**bad)


def test_a_secret_form_card_names_the_credential_and_a_fixed_endpoint():
    card = cards.secret_form_card("lab-sonic_1", username="admin", note="for the lab")
    assert card["interaction"] == "secret_form" and card["endpoint"] == cards.SECRET_ENDPOINT
    assert (card["credential_name"], card["credential_kind"], card["username"]) == ("lab-sonic_1", "password", "admin")
    assert all(isinstance(v, str) for v in card.values())
    # There is no field the model could use to carry or aim a secret.
    assert not {"secret", "password", "payloads", "options"} & set(card)


def test_a_secret_form_card_refuses_a_name_the_executor_would_refuse():
    for bad in ("", "has space", "a/b", "x" * 61, "pw=Zq9-fake-Pass-1234"):
        with pytest.raises(cards.CardRejected):
            cards.secret_form_card(bad)
    with pytest.raises(cards.CardRejected):
        cards.secret_form_card("lab", kind="plaintext")


def test_a_change_card_carries_diff_rollback_and_the_text_to_confirm():
    card = _decision(
        diff=["--- before", "+++ after", "+vlan 100"] + [f"+x{i}" for i in range(500)],
        rollback=["config vlan del 100"],
        confirm="lab-sonic",
        tier="change-high",
    )
    diff = json.loads(card["diff"])
    assert diff[:3] == ["--- before", "+++ after", "+vlan 100"]
    assert diff[-1].startswith("... ") and len(card["diff"]) <= cards.VALUE_BUDGET  # cut to fit, not refused
    assert json.loads(card["rollback"]) == ["config vlan del 100"]
    assert card["confirm"] == "lab-sonic" and card["tier"] == "change-high"
    assert {"diff", "rollback", "confirm"}.isdisjoint(_decision())
    with pytest.raises(cards.CardRejected):
        _decision(confirm="x" * 100)


AZURE_APPROVE = "plugins/_ea_azure_change/azure_approve"
REFERENCE = {"label": "CR or SR number", "pattern": "(CR|SR)-?[0-9]{4,10}", "example": "CR-123456"}
PDF = {"label": "Change document (PDF)", "payload": {"change_id": "c1", "version": "issued", "format": "pdf"}}


def test_an_azure_change_card_carries_the_reference_the_documents_and_an_edit():
    card = _decision(
        endpoint=AZURE_APPROVE,
        edit_endpoint="plugins/_ea_azure_change/azure_edit",
        reference=REFERENCE,
        documents=[PDF],
        checks=[{"label": "Advice: idle disk", "ok": True, "level": "warn"}],
        tier="change-high",
    )
    assert json.loads(card["reference"]) == REFERENCE
    assert json.loads(card["documents"]) == [PDF]
    assert card["document_endpoint"] == "plugins/_ea_azure_change/azure_document"
    assert json.loads(card["checks"])[0]["level"] == "warn"
    assert "confirm" not in card  # no typed count for Azure (D24)
    assert {"reference", "documents", "document_endpoint"}.isdisjoint(_decision())


def test_a_card_refuses_a_bad_reference_document_or_check_level():
    for bad in (
        {"reference": {**REFERENCE, "pattern": "(unclosed"}},
        {"reference": {**REFERENCE, "pattern": ""}},
        {"reference": {**REFERENCE, "pattern": "x" * 101}},
        {"reference": {"label": "", "pattern": "x"}},
        {"documents": [{"label": "No payload"}]},
        {"documents": [PDF] * 5},
        {"checks": [{"label": "x", "ok": True, "level": "crit"}]},
        {"tier": "suggestion"},  # a suggestion authorises nothing, so no endpoint
    ):
        with pytest.raises(cards.CardRejected):
            _decision(endpoint=AZURE_APPROVE, **bad)


def test_a_suggestion_is_a_question_card_with_no_endpoint():
    card = cards.question_card(
        "Suggestion: remove the idle disk",
        ["I will do this myself", "Not now"],
        details=[("Resources", "disk-1")],
        commands=["1. Snapshot the disk"],
        tier="suggestion",
        documents=[{"label": "Runbook (PDF)", "payload": {"change_id": "c1", "version": "runbook", "format": "pdf"}}],
    )
    assert card["interaction"] == "question" and card["tier"] == "suggestion"
    assert json.loads(card["documents"])[0]["payload"]["version"] == "runbook"
    assert not {"endpoint", "payloads", "reference"} & set(card)
    assert "tier" not in cards.question_card("Which?", ["a", "b"])


def _steps(count, lines=3, title="vnet in rg-hub"):
    return [{"n": i, "title": f"{title} {i}", "lines": [f"+ azurerm_thing.t{i}_{j}  name-{j}" for j in range(lines)]} for i in range(1, count + 1)]


def test_a_change_set_card_carries_its_steps_in_order():
    card = _decision(endpoint=AZURE_APPROVE, steps=_steps(3))
    rows = json.loads(card["steps"])
    assert [r["n"] for r in rows] == [1, 2, 3] and all(len(r["lines"]) == 3 for r in rows)
    assert "steps" not in _decision()


def test_twelve_steps_fit_and_the_cut_lines_say_how_many_in_every_step_alike():
    card = _decision(endpoint=AZURE_APPROVE, steps=_steps(12, lines=40))
    assert len(card["steps"]) <= cards.VALUE_BUDGET
    rows = json.loads(card["steps"])
    assert len(rows) == 12
    for row in rows:
        kept = [l for l in row["lines"] if not l.startswith("...")]
        assert len(kept) >= 3  # dealt out evenly, so none is bare while another keeps all
        assert row["lines"][-1] == f"... {40 - len(kept)} more lines in the change document"


def test_a_step_title_is_refused_never_cut_and_so_are_bad_steps():
    cases = (
        _steps(13),
        _steps(2, title="x" * 120),  # 120 + the number is over
        [{"n": 2, "title": "t", "lines": []}],  # not numbered from 1
        [{"n": 1, "title": "", "lines": []}],
        [{"n": 1, "title": "t", "lines": [], "adds": -1}],
        "not a list",
    )
    for bad in cases:
        with pytest.raises(cards.CardRejected):
            _decision(endpoint=AZURE_APPROVE, steps=bad)
    assert json.loads(_decision(endpoint=AZURE_APPROVE, steps=_steps(1, title="x" * 100))["steps"])[0]["title"].endswith("1")


def test_the_most_steps_is_the_executors():
    from cloud_executor import gate

    assert cards.STEPS_MAX == gate.SET_MAX_STEPS


def test_the_interface_holds_the_same_list_and_draws_cards_only_from_card_steps():
    """The allowlist above is checked where the server builds a card. A card is
    drawn in the interface from a step's values, and every tool call is logged
    with its arguments -- the model's to write -- as its values. The interface
    took a card from any step and posted a decision to whatever endpoint the
    values named, with the viewer's session; so the list has to hold there too,
    and a tool step must never be a card (the vitest suite proves the
    behaviour; this keeps the two lists one list)."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "frontend/src/lib/transcript.ts").read_text(encoding="utf-8")
    block = source[source.index("export const DECISION_ENDPOINTS") :]
    listed = set(re.findall(r'"(plugins/[^"]+)"', block[: block.index("];")]))

    assert listed == set(cards.AUTHORISED_ENDPOINTS)
    assert "if (!DECISION_ENDPOINTS.includes(endpoint)" in source
    assert 'const CARD_STEP_TYPE = "info";' in source
    # Every card the server writes is written as that step type.
    assert 'type="info"' in Path(cards.__file__).read_text(encoding="utf-8")
