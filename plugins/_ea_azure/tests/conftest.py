import importlib.util
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _no_query_pause(monkeypatch):
    """A pull paces its Resource Graph queries (15 per 5 seconds); no test waits for that unless it asks to."""
    from plugins._ea_azure.helpers import collect

    collect._sent.clear()
    monkeypatch.setattr(collect, "_sleep", lambda seconds: collect._sent.clear())  # time does not pass, so forget them


@pytest.fixture(autouse=True)
def _label_stub(monkeypatch):
    """`categories.py` belongs to another worker; until it lands the store only needs `label_of`."""
    name = "plugins._ea_azure.helpers.categories"
    if importlib.util.find_spec(name) is None:
        stub = types.ModuleType(name)
        stub.label_of = lambda type_, kind="": type_.rsplit("/", 1)[-1]
        monkeypatch.setitem(sys.modules, name, stub)
