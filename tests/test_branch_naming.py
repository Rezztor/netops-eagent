"""Naming a branched conversation.

The name used to be the parent's plus "(branch)", which compounds. Branching a
branch gave "Chat (branch) (branch)", and a real sidebar reached
"Chat (branch) (branch) (branch) (branch) (branch) (branch)" -- six copies of a
word that distinguishes nothing, with the part naming the conversation pushed
out of the column.
"""

import pytest

from plugins._chat_branching.api.branch_chat import _branch_name


def test_the_first_branch_of_a_conversation():
    assert _branch_name("Deploy plan", []) == "Deploy plan (branch)"


def test_branching_a_branch_does_not_compound():
    """The whole point. This returned "Deploy plan (branch) (branch)"."""
    assert _branch_name("Deploy plan (branch)", ["Deploy plan (branch)"]) == "Deploy plan (branch 2)"


def test_six_rounds_stay_readable():
    existing = ["Deploy plan"]
    name = "Deploy plan"
    for _ in range(6):
        name = _branch_name(name, existing)
        existing.append(name)
    assert existing[1:] == [
        "Deploy plan (branch)",
        "Deploy plan (branch 2)",
        "Deploy plan (branch 3)",
        "Deploy plan (branch 4)",
        "Deploy plan (branch 5)",
        "Deploy plan (branch 6)",
    ]


def test_numbering_is_by_sibling_not_by_depth():
    """Branching the parent again, with one branch already taken, continues the
    series rather than colliding with it."""
    existing = ["Deploy plan", "Deploy plan (branch)"]
    assert _branch_name("Deploy plan", existing) == "Deploy plan (branch 2)"


def test_a_gap_in_the_series_is_reused():
    existing = ["Deploy plan (branch)", "Deploy plan (branch 3)"]
    assert _branch_name("Deploy plan", existing) == "Deploy plan (branch 2)"


def test_another_conversations_branches_do_not_shift_the_number():
    existing = ["Rollback (branch)", "Rollback (branch 2)"]
    assert _branch_name("Deploy plan", existing) == "Deploy plan (branch)"


def test_a_name_that_merely_contains_the_word_is_left_alone():
    assert _branch_name("The (branch) office plan", []) == "The (branch) office plan (branch)"


@pytest.mark.parametrize("empty", ["", None, "   "])
def test_an_unnamed_conversation_still_gets_a_name(empty):
    assert _branch_name(empty, []) == "Chat (branch)"


def test_a_name_that_is_only_a_suffix_falls_back():
    """Stripping "(branch)" from "(branch)" leaves nothing to name it with."""
    assert _branch_name("(branch)", []) == "Chat (branch)"


def test_unnamed_siblings_are_counted_too():
    assert _branch_name("Chat (branch)", ["Chat (branch)"]) == "Chat (branch 2)"


def test_a_sibling_list_with_junk_in_it_does_not_raise():
    assert _branch_name("Deploy plan", [None, "", "Deploy plan (branch)"]) == "Deploy plan (branch 2)"


# --- names already in the wild ---------------------------------------------


def test_a_name_carrying_a_pile_of_suffixes_collapses_to_its_subject():
    """Real data: six rounds of the old behaviour. Peeling one suffix would
    leave five, so these would stop growing without ever getting shorter."""
    existing = ["Chat (branch)", "Chat (branch) (branch)", "Chat (branch) (branch) (branch)"]
    assert _branch_name("Chat (branch) (branch) (branch)", existing) == "Chat (branch 2)"


def test_every_survivor_of_the_old_scheme_counts_as_branch_one():
    from plugins._chat_branching.api.branch_chat import _split_branch

    for name in ("Chat (branch)", "Chat (branch) (branch)", "Chat (branch) (branch) (branch)"):
        assert _split_branch(name) == ("Chat", 1)


def test_the_series_continues_past_the_old_names():
    existing = [
        "Chat (branch)",
        "Chat (branch) (branch)",
        "Chat (branch) (branch) (branch)",
        "Chat (branch 2)",
    ]
    assert _branch_name("Chat (branch) (branch)", existing) == "Chat (branch 3)"


def test_a_subject_with_its_own_number_is_not_mistaken_for_a_branch():
    assert _branch_name("Sprint 3", []) == "Sprint 3 (branch)"
