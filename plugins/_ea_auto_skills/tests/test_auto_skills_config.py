import pytest
from plugins._ea_auto_skills.helpers import config


def test_the_shipped_default_is_off(monkeypatch):
    """An upgrade must not switch the feature on for an instance that never
    asked for it: every other mode spends a model call per qualifying turn.

    Stubs the stored config rather than reading it. get_plugin_config falls back
    to the shipped defaults only when no config.json exists anywhere, so without
    this the assertion would pass or fail on whatever mode the machine running
    the tests happens to be set to.
    """
    from helpers import plugins as plugin_helpers

    shipped = plugin_helpers.get_default_plugin_config(config.PLUGIN_NAME)
    assert shipped["mode"] == config.MODE_OFF

    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: shipped)
    assert config.get_config()["mode"] == config.MODE_OFF


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("personal", config.MODE_PERSONAL),
        ("review", config.MODE_REVIEW),
        ("off", config.MODE_OFF),
        ("  PERSONAL  ", config.MODE_PERSONAL),
        # YAML reads a bare off/no as False and on/yes as True, so a hand-edited
        # config can deliver a boolean where a mode name was meant.
        (False, config.MODE_OFF),
        (True, config.MODE_OFF),
        ("nonsense", config.MODE_OFF),
        (None, config.MODE_OFF),
        (3, config.MODE_OFF),
    ],
)
def test_an_unrecognised_mode_stops_the_plugin_rather_than_starting_it(
    stored, expected, monkeypatch
):
    monkeypatch.setattr(
        "helpers.plugins.get_plugin_config", lambda *a, **k: {"mode": stored}
    )
    assert config.get_config()["mode"] == expected


@pytest.mark.parametrize("key", config._INT_KEYS)
def test_a_nonsense_number_falls_back_rather_than_disabling_a_limit(key, monkeypatch):
    monkeypatch.setattr(
        "helpers.plugins.get_plugin_config", lambda *a, **k: {key: "not a number"}
    )
    assert config.get_config()[key] == config._FALLBACK[key]


def test_a_negative_limit_is_refused(monkeypatch):
    """A negative cap would read as "no limit" everywhere it is compared."""
    monkeypatch.setattr(
        "helpers.plugins.get_plugin_config", lambda *a, **k: {"max_personal_skills": -1}
    )
    assert config.get_config()["max_personal_skills"] == config._FALLBACK["max_personal_skills"]
