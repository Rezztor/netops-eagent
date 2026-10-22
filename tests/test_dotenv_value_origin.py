"""Where a configuration value comes from.

This exists because the two sources silently disagree. `load_dotenv` is called
with `override=True`, so `usr/.env` -- written by the settings interface and
living on the persistent volume -- beats a value injected from the process
environment. An operator who rotates an injected variable and redeploys gets an
instance still using the old key, with nothing on screen to say so, unless the
origin is surfaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import dotenv  # noqa: E402


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(path))
    return path


def test_a_value_in_the_file_is_reported_as_coming_from_the_file(env_file, monkeypatch):
    env_file.write_text("API_KEY_EXAMPLE=from-the-file\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY_EXAMPLE", raising=False)
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_FILE


def test_the_file_wins_over_the_environment(env_file, monkeypatch):
    """Not a preference -- it is what load_dotenv(override=True) does, and the
    interface must report the source that actually applies."""
    env_file.write_text("API_KEY_EXAMPLE=from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("API_KEY_EXAMPLE", "from-the-environment")
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_FILE


def test_a_value_only_in_the_environment_is_reported_as_such(env_file, monkeypatch):
    monkeypatch.setenv("API_KEY_EXAMPLE", "from-the-environment")
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_ENVIRONMENT


def test_an_empty_file_entry_is_not_a_configured_value(env_file, monkeypatch):
    """The settings writer records a line for every known provider whether or
    not a key was supplied, so usr/.env normally holds thirty-odd blank
    `API_KEY_X=` lines. Counting those reports every provider as configured."""
    env_file.write_text("API_KEY_EXAMPLE=\nAPI_KEY_OTHER=   \n", encoding="utf-8")
    monkeypatch.delenv("API_KEY_EXAMPLE", raising=False)
    monkeypatch.delenv("API_KEY_OTHER", raising=False)
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_UNSET
    assert dotenv.value_origin("API_KEY_OTHER") == dotenv.ORIGIN_UNSET


def test_an_empty_environment_value_is_not_a_configured_value(env_file, monkeypatch):
    """load_dotenv(override=True) copies the blank lines above into the
    environment, so presence in os.environ is not enough either."""
    monkeypatch.setenv("API_KEY_EXAMPLE", "")
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_UNSET


def test_an_unknown_key_is_unset(env_file, monkeypatch):
    monkeypatch.delenv("API_KEY_NOT_PRESENT", raising=False)
    assert dotenv.value_origin("API_KEY_NOT_PRESENT") == dotenv.ORIGIN_UNSET


def test_comments_and_malformed_lines_are_ignored(env_file, monkeypatch):
    env_file.write_text(
        "# API_KEY_COMMENTED=looks-real\nnot-an-assignment\nAPI_KEY_REAL=value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("API_KEY_COMMENTED", raising=False)
    assert dotenv.value_origin("API_KEY_COMMENTED") == dotenv.ORIGIN_UNSET
    assert dotenv.value_origin("API_KEY_REAL") == dotenv.ORIGIN_FILE


def test_quoted_values_count_as_configured(env_file, monkeypatch):
    env_file.write_text('API_KEY_QUOTED="a-real-key"\n', encoding="utf-8")
    monkeypatch.delenv("API_KEY_QUOTED", raising=False)
    assert dotenv.value_origin("API_KEY_QUOTED") == dotenv.ORIGIN_FILE


def test_a_missing_env_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(tmp_path / "absent"))
    monkeypatch.delenv("API_KEY_EXAMPLE", raising=False)
    assert dotenv.value_origin("API_KEY_EXAMPLE") == dotenv.ORIGIN_UNSET


def test_settings_reports_an_origin_for_every_provider():
    """The interface shows this per provider, so a missing entry renders as
    'not set' for a provider that may well have a key."""
    from helpers import settings as settings_helper

    out = settings_helper.convert_out(settings_helper.get_default_settings())
    origins = out["additional"]["api_key_origins"]
    providers = {p["value"] for p in settings_helper.get_providers("chat")}
    providers |= {p["value"] for p in settings_helper.get_providers("embedding")}
    assert providers <= set(origins), sorted(providers - set(origins))
    assert set(origins.values()) <= {
        dotenv.ORIGIN_FILE,
        dotenv.ORIGIN_ENVIRONMENT,
        dotenv.ORIGIN_UNSET,
    }


def test_a_value_with_special_characters_is_read_back_unchanged_and_sets_nothing_else(tmp_path, monkeypatch):
    # Written bare, `#` cut the value, and a newline wrote a second key chosen by whoever set the password.
    import os

    from helpers import dotenv

    env = tmp_path / ".env"
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env))
    monkeypatch.delenv("INJECTED_KEY", raising=False)
    for value in ("plain-Value_1", "pa ss#word", 'q"uo\'te\\back', "line1\nINJECTED_KEY=yes", "${HOME}/x", ""):
        dotenv.save_dotenv_value("REZZTOR_TEST_SECRET", value)
        assert os.environ["REZZTOR_TEST_SECRET"] == value, value
        assert "INJECTED_KEY" not in os.environ
        assert len([line for line in env.read_text().splitlines() if line.strip()]) == 1
    monkeypatch.delenv("REZZTOR_TEST_SECRET", raising=False)


@pytest.mark.parametrize("value", ["abc\\", 'p"w\\', "has space\\", "\\"])
def test_a_value_ending_in_a_backslash_does_not_take_the_next_key_with_it(tmp_path, monkeypatch, value):
    """Between double quotes the value was written `"abc\\\\"`, and the reader
    takes that last `\\"` for an escaped quote. It then reads on to the next
    quote in the file: with a quoted line below, this value and the key on that
    line were both gone -- an administrator saved a password and a provider key
    two lines down stopped existing."""
    from dotenv import dotenv_values

    from helpers import dotenv

    env = tmp_path / ".env"
    env.write_text('SMTP_PASSWORD=old\nAPI_KEY_NEXT="a key with a space"\nOTHER=plain\n', encoding="utf-8")
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env))

    dotenv.save_dotenv_value("SMTP_PASSWORD", value, reload_env=False)

    assert dict(dotenv_values(str(env))) == {
        "SMTP_PASSWORD": value,
        "API_KEY_NEXT": "a key with a space",
        "OTHER": "plain",
    }


def test_a_value_that_no_spelling_reads_back_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    """Ending in a backslash rules out the quotes; a leading space rules out
    going without them. Refused, rather than written as something else."""
    from helpers import dotenv

    env = tmp_path / ".env"
    env.write_text("KEEP=1\n", encoding="utf-8")
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env))

    with pytest.raises(ValueError, match="backslash"):
        dotenv.save_dotenv_value("X", " leading space and\\", reload_env=False)
    assert env.read_text(encoding="utf-8") == "KEEP=1\n"


def test_two_saves_at_once_do_not_lose_each_others_key(tmp_path, monkeypatch):
    """usr/.env holds every provider key, and each save read the whole file,
    changed one line and wrote the whole file back, with nothing between two
    writers. One request saving a key while another saved the timezone -- which
    any account's page does -- could write the file back without the key."""
    import threading

    from dotenv import dotenv_values

    from helpers import dotenv

    env = tmp_path / ".env"
    env.write_text("".join(f"KEEP_{n}=value-{n}\n" for n in range(40)), encoding="utf-8")
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env))
    start = threading.Barrier(8)

    def save(worker: int) -> None:
        start.wait()
        for turn in range(25):
            dotenv.save_dotenv_value(f"KEY_{worker}_{turn}", f"v{worker}.{turn}", reload_env=False)

    threads = [threading.Thread(target=save, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stored = dotenv_values(str(env))
    missing = [f"KEY_{w}_{t}" for w in range(8) for t in range(25) if stored.get(f"KEY_{w}_{t}") != f"v{w}.{t}"]
    assert not missing, f"{len(missing)} of 200 saved values are not in the file"
    assert all(stored.get(f"KEEP_{n}") == f"value-{n}" for n in range(40))


def test_the_file_is_replaced_whole_and_kept_private(tmp_path, monkeypatch):
    """Written in place, a reader in another thread could find the file cut
    short. It holds credentials, so it is the account's alone."""
    import stat

    from helpers import dotenv

    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    before = env.stat().st_ino
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env))

    dotenv.save_dotenv_value("B", "2", reload_env=False)

    assert env.stat().st_ino != before, "the file was rewritten in place"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert not [name for name in tmp_path.iterdir() if name.name != ".env"], "a temporary file was left behind"


def test_a_process_that_never_loaded_the_file_keeps_the_stored_persistent_id(env_file, monkeypatch):
    """The appliance console asks the licence state with `python -c` in the container, a process that never ran
    load_dotenv. It found no id in its environment, made a new one and wrote it over the stored one; the server's
    next start, an update's restart, renamed the session cookie and signed everybody out."""
    from helpers import runtime

    env_file.write_text("REZZTOR_PERSISTENT_RUNTIME_ID=the-one-in-use\n", encoding="utf-8")
    monkeypatch.setenv("REZZTOR_PERSISTENT_RUNTIME_ID", "")
    monkeypatch.delenv("REZZTOR_PERSISTENT_RUNTIME_ID")

    assert runtime.get_persistent_id() == "the-one-in-use"
    assert env_file.read_text(encoding="utf-8") == "REZZTOR_PERSISTENT_RUNTIME_ID=the-one-in-use\n"
