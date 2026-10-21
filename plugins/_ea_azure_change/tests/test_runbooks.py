"""Every waste rule and Advisor category has a suggestion runbook the product cannot run."""
import re
from pathlib import Path

import yaml

from plugins._ea_azure.helpers.waste import RULES

BOOK = Path(__file__).resolve().parents[1] / "runbooks.yaml"
KEYS = ("rule", "title", "why_it_matters", "check_first", "portal_steps", "confirm", "risk", "steps")
# REST categories, lowercased as suggest does. Reliability is the portal name for HighAvailability.
# https://learn.microsoft.com/en-us/rest/api/advisor/recommendations/list
ADVISOR = ("cost", "security", "highavailability", "reliability", "performance", "operationalexcellence")
KIND = re.compile(r"[a-z0-9_]{1,40}")
NEVER = "the product never deletes, stops, deallocates, restarts, resizes, scales in or swaps"
# An instruction to the product or the agent. "never" is the boundary, not an order.
ORDER = re.compile(
    r"\b(the product|the agent|this agent|the tool)\s+"
    r"(?:to |will |should |must |can )?"
    r"(delete|stop|deallocate|restart|resize|scale in|swap)\w*",
    re.I,
)
TOOLS = ("azure_apply", "azure_action", "tofu destroy", "az disk delete", "az vm deallocate", "az vm delete", "az vm stop")


def _books():
    data = yaml.safe_load(BOOK.read_text())
    assert isinstance(data, dict) and data
    return data


def test_every_waste_rule_and_advisor_category_has_a_filled_runbook():
    data = _books()
    missing = [name for name in (*RULES, *ADVISOR) if name not in data]
    assert not missing, missing
    for name, entry in data.items():
        assert KIND.fullmatch(name), name
        assert entry["rule"] == name
        for key in KEYS:
            value = entry[key]
            if key in ("portal_steps", "steps"):
                assert value and all(isinstance(line, str) and line.strip() for line in value), f"{name}.{key}"
            else:
                assert isinstance(value, str) and value.strip(), f"{name}.{key}"
        shown = "\n".join(entry["steps"])
        blob = shown + "\n" + "\n".join(entry["portal_steps"]) + "\n" + entry["check_first"] + entry["risk"]
        assert "{names}" in shown, name
        assert all(len(line) <= 280 for line in entry["steps"]), name
        assert NEVER in shown.lower(), name
        assert "engineer" in blob.lower(), name
        assert ORDER.search(blob) is None, name
        lowered = blob.lower()
        for tool in TOOLS:
            assert tool not in lowered, f"{name} names {tool}"
