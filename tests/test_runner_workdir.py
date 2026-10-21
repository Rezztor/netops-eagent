"""Where agent code runs.

The application container and the runner share no volume -- deliberately, so the
runner cannot reach `usr/`. That makes an application-container path meaningless
on the runner, and one was being handed over: `cd /eagent/usr/workdir/<user>`
against a read-only root, failing, with the session carrying on in the login home
and every subsequent command landing somewhere the tool did not think it was.
"""

import shlex

import pytest

from helpers import user_scope
from plugins._code_execution.tools.code_execution_tool import (
    RUNNER_WORKSPACE,
    CodeExecution,
)


class _Context:
    def __init__(self):
        self.id = "ctx-1"
        self._data = {}

    def get_data(self, key):
        return self._data.get(key)

    def set_data(self, key, value):
        self._data[key] = value


class _Agent:
    def __init__(self):
        self.context = _Context()


def _tool():
    tool = CodeExecution.__new__(CodeExecution)
    tool.agent = _Agent()  # type: ignore[attr-defined]
    return tool


@pytest.fixture
def no_project(monkeypatch):
    from helpers import projects

    monkeypatch.setattr(projects, "get_context_project_name", lambda ctx: None)


def test_the_runner_cwd_is_a_runner_path(no_project):
    with user_scope.user_scope("alice"):
        cwd = _tool()._runner_cwd()
    assert cwd == f"{RUNNER_WORKSPACE}/alice"


def test_it_is_never_an_application_container_path(no_project):
    """The specific defect: /eagent does not exist on the runner and its root is
    read-only, so this path produced `mkdir: Read-only file system`."""
    with user_scope.user_scope("alice"):
        cwd = _tool()._runner_cwd()
    assert "/eagent" not in cwd
    assert "usr/workdir" not in cwd
    assert cwd.startswith(RUNNER_WORKSPACE + "/")


def test_two_users_do_not_share_a_working_directory(no_project):
    """On Compose one runner serves every account, so this is the only thing
    keeping one user's agent out of another's files."""
    with user_scope.user_scope("alice"):
        alice = _tool()._runner_cwd()
    with user_scope.user_scope("bob"):
        bob = _tool()._runner_cwd()
    assert alice != bob
    assert not alice.startswith(bob) and not bob.startswith(alice)


def test_no_owner_is_refused_rather_than_shared(no_project):
    """Collapsing onto the shared workspace would hide the bug that lost the
    scope, and silently put two accounts in one directory."""
    with pytest.raises(user_scope.UserScopeError):
        _tool()._runner_cwd()


def test_a_project_nests_inside_its_owner(monkeypatch):
    from helpers import projects

    monkeypatch.setattr(projects, "get_context_project_name", lambda ctx: "atlas")
    with user_scope.user_scope("alice"):
        cwd = _tool()._runner_cwd()
    assert cwd == f"{RUNNER_WORKSPACE}/alice/atlas"


def test_a_project_name_cannot_escape_the_owner(monkeypatch):
    from helpers import projects

    monkeypatch.setattr(projects, "get_context_project_name", lambda ctx: "../bob")
    with user_scope.user_scope("alice"):
        with pytest.raises(ValueError):
            _tool()._runner_cwd()


@pytest.mark.asyncio
async def test_remote_execution_never_takes_the_local_branch(no_project, monkeypatch):
    """The local branch creates the directory through the application container,
    which is the wrong filesystem and the reason empty per-user directories
    accumulated under usr/workdir."""
    called = []
    from helpers import runtime

    monkeypatch.setattr(
        runtime,
        "call_development_function",
        lambda *a, **k: called.append(a),
    )
    with user_scope.user_scope("alice"):
        cwd = await _tool().ensure_cwd(ssh_enabled=True)
    assert cwd == f"{RUNNER_WORKSPACE}/alice"
    assert called == []


def test_the_runner_session_creates_and_enters_the_directory():
    """Nothing else can: the caller only reaches the application container."""
    from plugins._code_execution.helpers.shell_openssh import cwd_preamble

    preamble = cwd_preamble("/workspace/alice", "MARK")
    assert "mkdir -p /workspace/alice" in preamble
    assert "cd /workspace/alice" in preamble
    # `;` between cd and the rest was the original bug -- a failed cd left the
    # shell in the login home and said nothing.
    assert "|| echo MARK" in preamble


def test_a_working_directory_with_a_space_is_quoted():
    from plugins._code_execution.helpers.shell_openssh import cwd_preamble

    preamble = cwd_preamble("/workspace/al ice", "MARK")
    assert "/workspace/al ice" not in preamble.replace("'/workspace/al ice'", "")


def test_the_preamble_actually_reports_a_directory_it_cannot_enter():
    """Run it, rather than trusting the string."""
    import subprocess

    from plugins._code_execution.helpers.shell_openssh import cwd_preamble

    # /proc rejects mkdir, which is what a read-only runner root does too.
    out = subprocess.run(
        ["bash", "-c", cwd_preamble("/proc/nope/deeper", 'MARK_""1') + "echo done"],
        capture_output=True,
        text=True,
    )
    assert "MARK_1" in out.stdout
    assert "done" in out.stdout


def test_the_preamble_is_silent_when_the_directory_works(tmp_path):
    import subprocess

    from plugins._code_execution.helpers.shell_openssh import cwd_preamble

    target = tmp_path / "alice"
    out = subprocess.run(
        ["bash", "-c", cwd_preamble(str(target), 'MARK_""1') + "pwd"],
        capture_output=True,
        text=True,
    )
    assert "MARK_1" not in out.stdout
    assert out.stdout.strip().endswith("alice")
