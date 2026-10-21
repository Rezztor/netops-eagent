"""Shared fixtures for the executor's tests.

The executor is a module-level Flask app with a module-level scheduler, which is
right for a process that serves one purpose and wrong for a test suite that runs
hundreds of them in one interpreter. Two pieces of state leak between tests
unless something takes them apart: the job store, which otherwise opens at
`/var/lib/eagent-ansible` and fails, and the scheduler's count of what is running.

The second is the subtle one. A job thread that outlives its test finds the store
closed, dies, and never tells the scheduler it finished -- so the next test's job
is queued behind a job that no longer exists, and the failure surfaces as an
unrelated test hanging. Draining before teardown is what stops one test's threads
becoming another test's mystery.
"""

from __future__ import annotations

import time

import pytest

from executor import app as executor_app
from executor import credentials as executor_credentials
from executor import inventory as executor_inventory
from executor import store as executor_store

TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ADMIN_TOKEN = "test-admin-token-value"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

#: 32 bytes, base64. Fixed rather than random so a failure is reproducible.
CREDENTIAL_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def drain(seconds: float = 5.0) -> None:
    """Wait for the scheduler to have nothing running and nothing waiting."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        depth = executor_app._scheduler.depth()
        if depth == {"running": 0, "queued": 0}:
            return
        time.sleep(0.01)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(executor_app.ENV_TOKEN, TOKEN)
    monkeypatch.delenv(executor_app.ENV_COMMIT, raising=False)
    # The store defaults to /var/lib/eagent-ansible, which exists in the image
    # and nowhere else. Every test gets its own file.
    monkeypatch.setenv(executor_app.ENV_ADMIN_TOKEN, ADMIN_TOKEN)
    monkeypatch.setenv(executor_credentials.ENV_KEY, CREDENTIAL_KEY)
    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    executor_credentials.reset_for_tests(str(tmp_path / "credentials.json"))
    executor_inventory.reset_for_tests(str(tmp_path / "inventory.json"))
    executor_app._scheduler.reset_for_tests()
    executor_app.app.config.update(TESTING=True)
    try:
        yield executor_app.app.test_client()
    finally:
        drain()
        executor_app._scheduler.reset_for_tests()
        executor_store.reset_for_tests(None)
        executor_credentials.reset_for_tests(None)
        executor_inventory.reset_for_tests(None)
