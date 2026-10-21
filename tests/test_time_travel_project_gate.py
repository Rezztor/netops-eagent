"""Rolling a project's files back is changing the project.

`api/projects.py` lets only an administrator change a project, because its
instructions, knowledge and skills reach every account that opens it. Time
travel keeps history of those same files (`.eaproj/instructions/`,
`knowledge/`, `skills/`, `agents.json`) and its `history_revert` and
`history_travel` endpoints write them back, for any signed-in account: the
`user` role could undo an administrator's change to a project, or put an
older set of instructions in front of everybody.
"""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from helpers import login, rbac
from plugins._time_travel.api import history_revert, history_travel
from plugins._time_travel.helpers.time_travel import WorkspaceInfo

HANDLERS = [(history_revert, "HistoryRevert", "revert"), (history_travel, "HistoryTravel", "travel")]


def _workspace(project_name: str) -> WorkspaceInfo:
    return WorkspaceInfo(
        id="w1",
        display_path="/eagent/usr/x",
        real_path=Path("/nonexistent"),
        shadow_path=Path("/nonexistent"),
        repo_git_path=Path("/nonexistent"),
        project_name=project_name,
    )


def _ask(monkeypatch, module, class_name, method, *, project, administrator):
    done = []
    service = SimpleNamespace(**{method: lambda **kwargs: done.append(method) or {"ok": True}})
    monkeypatch.setattr(module, "resolve_workspace", lambda *args, **kwargs: _workspace(project))
    monkeypatch.setattr(module, "TimeTravelService", lambda workspace: service)
    monkeypatch.setattr(
        login, "has_permission", lambda permission: administrator and permission == rbac.PERM_SETTINGS_MANAGE
    )
    handler = getattr(module, class_name)(Flask("time_travel_gate"), threading.Lock())
    answer = asyncio.run(handler.process({"context_id": "c", "workspace_id": "w1", "commit_hash": "abc"}, None))
    return answer, done


@pytest.mark.parametrize("module, class_name, method", HANDLERS)
def test_an_ordinary_account_cannot_rewrite_a_projects_files(monkeypatch, module, class_name, method):
    answer, done = _ask(monkeypatch, module, class_name, method, project="shared", administrator=False)
    assert answer["ok"] is False and "administrator" in answer["error"]
    assert done == []


@pytest.mark.parametrize("module, class_name, method", HANDLERS)
def test_an_administrator_can(monkeypatch, module, class_name, method):
    answer, done = _ask(monkeypatch, module, class_name, method, project="shared", administrator=True)
    assert answer == {"ok": True} and done == [method]


@pytest.mark.parametrize("module, class_name, method", HANDLERS)
def test_an_accounts_own_working_folder_stays_its_own_to_rewrite(monkeypatch, module, class_name, method):
    answer, done = _ask(monkeypatch, module, class_name, method, project="", administrator=False)
    assert answer == {"ok": True} and done == [method]
