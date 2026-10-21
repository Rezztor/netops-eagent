"""Every capability template is a fixed string plus bound parameters.

The property under test throughout: build a capability with an argument that
looks like an injection attempt, and the returned SWQL text must be byte-for-
byte identical to what the same capability produces for an innocuous argument
of the same shape. Only `params` may differ.
"""

from __future__ import annotations

import pytest

from plugins._ea_solarwinds.helpers import capabilities

INJECTION_ATTEMPTS = [
    "'; DROP TABLE Orion.Nodes; --",
    "x' OR '1'='1",
    "core-switch-1 UNION SELECT Password FROM Orion.Websites.Accounts",
]


@pytest.fixture(autouse=True)
def fixed_row_cap(monkeypatch):
    """Templates ask `config.get_config()` for the row cap. Fixed here so a
    test does not depend on default_config.yaml being readable from wherever
    pytest happens to run."""
    from plugins._ea_solarwinds.helpers import config as plugin_config

    monkeypatch.setattr(plugin_config, "get_config", lambda: {"max_rows": 25})


def _sample_args(capability: capabilities.Capability, value: str) -> dict:
    return {name: value for name in (*capability.required, *capability.optional)}


@pytest.mark.parametrize("name", capabilities.names())
def test_every_capability_is_listed_and_buildable(name):
    """Every entry in the public list actually builds, with a plausible
    argument for whatever it declares it needs."""
    args = _sample_args(capabilities._CAPABILITIES[name], "core-switch-1")
    built = capabilities.build(name, args)
    assert built.swql
    assert isinstance(built.params, dict)


@pytest.mark.parametrize("name", capabilities.names())
@pytest.mark.parametrize("attempt", INJECTION_ATTEMPTS)
def test_no_argument_value_ever_reaches_the_query_text(name, attempt):
    """The whole guarantee, exercised directly: whatever the caller supplies
    as an argument value must appear only in `params`, never woven into the
    SWQL string itself."""
    entry = capabilities._CAPABILITIES[name]
    args = _sample_args(entry, attempt)
    if not args:
        pytest.skip(f"{name} takes no arguments to attempt injection through")
    built = capabilities.build(name, args)
    assert attempt not in built.swql, (
        f"{name} wove an argument value into its SWQL text: {built.swql!r}"
    )


@pytest.mark.parametrize("name", capabilities.names())
def test_the_query_text_does_not_depend_on_the_arguments_content(name):
    """Same shape of input, two different values -- the SWQL text this
    produces must be identical. Only an *optional* argument's presence, never
    any argument's value, may change which literal template is used."""
    entry = capabilities._CAPABILITIES[name]
    args_a = _sample_args(entry, "alpha-device")
    args_b = _sample_args(entry, "totally-different-value-here")
    if not args_a:
        pytest.skip(f"{name} takes no arguments")
    built_a = capabilities.build(name, args_a)
    built_b = capabilities.build(name, args_b)
    assert built_a.swql == built_b.swql


def test_the_row_cap_comes_from_config_not_the_agent():
    built = capabilities.build("node_search", {"fragment": "sw"})
    assert "TOP 25" in built.swql
    assert "fragment" not in built.params  # bound as "pattern", not passed raw


def test_an_unknown_capability_names_what_is_available():
    with pytest.raises(capabilities.UnknownCapability, match="not a capability"):
        capabilities.build("delete_everything", {})


def test_a_missing_required_argument_is_refused_before_any_swql_is_built():
    with pytest.raises(capabilities.CapabilityError, match="'node' is required"):
        capabilities.build("node_status", {})


def test_active_alerts_scopes_to_a_node_only_when_one_is_given():
    """The one capability with a genuinely optional filter -- two different,
    both-fixed templates, chosen by presence, not by the value."""
    unscoped = capabilities.build("active_alerts", {})
    scoped = capabilities.build("active_alerts", {"node": "core-switch-1"})
    assert "WHERE" not in unscoped.swql
    assert "WHERE" in scoped.swql
    assert unscoped.swql != scoped.swql


def test_hardware_health_without_a_category_still_builds():
    """HardwareCategoryName is always selected and sorted on -- what must be
    absent without a category argument is the *filter* on it."""
    built = capabilities.build("hardware_health", {"node": "sw1"})
    assert "AND HardwareItem.HardwareCategoryName LIKE" not in built.swql
    assert built.params == {"pattern": "%sw1%"}


def test_hardware_health_with_a_category_filters_on_it():
    built = capabilities.build("hardware_health", {"node": "sw1", "category": "Temperature"})
    assert "AND HardwareItem.HardwareCategoryName LIKE @category" in built.swql
    assert built.params["category"] == "%Temperature%"


def test_device_config_never_mentions_a_live_download():
    """The distinction the plan is explicit about: this reads what NCM already
    archived, and the template must not be capable of triggering a fresh
    pull -- there is no verb in this SWQL that could mean that."""
    built = capabilities.build("device_config", {"node": "sw1"})
    assert "ConfigArchive" in built.swql
    lowered = built.swql.lower()
    assert "download" not in lowered or "downloadtime" in lowered


def test_a_pattern_argument_is_wrapped_for_a_like_match():
    built = capabilities.build("node_search", {"fragment": "core"})
    assert built.params["pattern"] == "%core%"


def test_an_exact_match_argument_is_not_wildcarded():
    built = capabilities.build("netflow_by_ip", {"ip": "10.0.0.1"})
    assert built.params["ip"] == "10.0.0.1"


def test_describe_lists_every_capability_with_its_arguments():
    described = {entry["name"]: entry for entry in capabilities.describe()}
    assert set(described) == set(capabilities.names())
    assert described["node_status"]["required"] == ["node"]
    assert described["active_alerts"]["optional"] == ["node"]


def test_device_config_is_five_per_call_cut_to_lines_and_pages_by_before():
    from plugins._ea_solarwinds.tools.solarwinds_lookup import _format

    built = capabilities.build("device_config", {"node": "sw1"})
    assert "TOP 5 " in built.swql and "@before" not in built.swql
    older = capabilities.build("device_config", {"node": "sw1", "before": "2026-01-02T03:04:05.000"})
    assert "Archive.DownloadTime < @before" in older.swql and older.params["before"] == "2026-01-02T03:04:05.000"
    rows = [{"NodeCaption": "sw1", "DownloadTime": f"2026-01-0{n}T00:00:00", "Config": "\n".join(f"line {i}" for i in range(250))} for n in range(5, 0, -1)]
    text = _format("device_config", rows)
    assert text.count("50 more lines not shown") == 5 and "line 200" not in text
    assert "before=2026-01-01T00:00:00" in text
