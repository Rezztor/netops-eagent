import pytest

from plugins._audit.helpers import audit
from plugins._ea_inventory.helpers import proposals


@pytest.fixture
def events(monkeypatch):
    """Audit events emitted during a test."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "emit", lambda event, **f: seen.append((event, f)))
    return seen


@pytest.fixture
def make(database, events):
    """Propose and approve a device in one go."""
    def _make(name="sw1", actor="alice", **fields):
        p = proposals.propose("create", None, {"name": name, **fields}, actor=actor)
        return proposals.decide(p["id"], "approve", actor=actor)[1]
    return _make
