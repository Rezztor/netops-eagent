"""The finished line of memory's housekeeping reads plainly; the counts stay in the folded details."""

import importlib

import pytest

BASE = "plugins._memory.extensions.python.monologue_end."


@pytest.mark.parametrize(
    "module, heading",
    [("_50_memorize_fragments", "Saved what it learned"), ("_51_memorize_solutions", "Saved what worked")],
)
def test_the_finished_heading_has_no_counts_or_jargon(module, heading):
    assert importlib.import_module(BASE + module).DONE_HEADING == heading


@pytest.mark.parametrize("module", ["_50_memorize_fragments", "_51_memorize_solutions"])
def test_the_counts_are_still_in_the_details(module):
    source = importlib.import_module(BASE + module).__file__
    text = open(source).read()
    assert "heading=DONE_HEADING" in text
    assert "intelligently consolidated" in text  # in `result`, the folded details
    assert 'heading=f"' + "Solution memorization" not in text and 'heading=f"Memorization completed' not in text
