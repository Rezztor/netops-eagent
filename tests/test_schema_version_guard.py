"""An older build must refuse a schema a newer one wrote.

`migrate()` applies what is missing, which is the whole story going forwards and
silence going backwards. Rolling REZZTOR_VERSION back after an upgrade starts the
previous image against the newer schema: it finds nothing to apply, comes up
clean, and then meets columns and constraints it does not know at runtime.

ADR 0037 named this exactly -- "a helm rollback after a failed start left the old
application on the new schema" -- and the appliance engine that answered it, by
restoring a dump first, was retired with k3s on 2026-09-18. Compose has no
rollback story, so the refusal is the story.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import db  # noqa: E402

GUARD = db._refuse_a_schema_this_build_does_not_know


def available(*numbers: int) -> list[tuple[int, str, str]]:
    return [(n, f"m{n}", f"/nowhere/{n}.sql") for n in numbers]


def test_a_schema_newer_than_the_build_is_refused():
    with pytest.raises(db.DatabaseError) as error:
        GUARD({1, 2, 3, 4}, available(1, 2, 3))

    message = str(error.value)
    assert "0004" in message, message
    # The operator has to be told what to do, not only that something is wrong.
    assert "restore" in message.lower() or "newer release" in message.lower()


def test_the_matching_schema_is_allowed():
    GUARD({1, 2, 3}, available(1, 2, 3))


def test_a_schema_behind_the_build_is_allowed():
    """The ordinary upgrade: this is what migrate() exists to do."""
    GUARD({1, 2}, available(1, 2, 3))


def test_a_fresh_database_is_allowed():
    """No rows yet means nothing has been applied, not that everything is
    unknown -- bootstrapping must not trip the guard."""
    GUARD(set(), available(1, 2, 3))


def test_a_gap_below_the_high_water_mark_is_not_a_refusal():
    """A number this build ships but has not applied is migrate()'s job, not a
    rollback: only numbers the build does not have at all are the signal."""
    GUARD({1, 3}, available(1, 2, 3))


def test_the_real_migration_set_passes_its_own_guard():
    """Guards against the guard: if `migrations()` and this ever disagree about
    numbering, every start fails."""
    numbers = {number for number, _, _ in db.migrations()}
    assert numbers, "no migrations found"
    GUARD(numbers, db.migrations())
