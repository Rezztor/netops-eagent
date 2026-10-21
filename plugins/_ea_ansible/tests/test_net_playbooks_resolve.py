"""Every module a catalogue playbook names exists in the executor image.

`reads/device_facts` called `ansible.netcommon.gather_facts` for as long as it
existed. That is not a module netcommon ships -- it has sixteen and that is not
one of them -- so every job died with `couldn't resolve module/action` before
reaching a device. The entry had never succeeded on any platform and could not
have.

`test_net_catalogue.py` did not catch it and was never going to: it checks entry
shapes, variable declarations and the `hosts: all` pattern, all of which that
playbook satisfied perfectly while naming a module that does not exist. The
catalogue's own comment said these entries "resolve in the built image", which
was the belief, not a check. A human found it by running it against live
hardware on 2026-09-19.

This is that belief made mechanical. It needs the built executor image, because
the collections are pinned there and nowhere else -- `ansible-galaxy` installs
them in the Dockerfile -- so it skips with the build command when the image is
absent, the way tests/test_runner_sandbox.py does for the runner.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
PLAYBOOK_DIR = os.path.join(PLUGIN_ROOT, "playbooks")
IMAGE = "rezztor/eagent-ansible:latest"

BUILD_HINT = (
    "build it first: docker build -f executor/Dockerfile -t "
    f"{IMAGE} --build-arg CATALOGUE_COMMIT=\"$(git rev-parse HEAD)\" ."
)


def _image_exists() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, text=True
    )
    return result.returncode == 0


def _module_names() -> set[str]:
    """Every `namespace.collection.module` a task in any playbook calls.

    A task's module is whichever key is not a task keyword. Only dotted names
    are returned: `ansible.builtin.debug` is worth checking and `when` is not.
    """
    keywords = {
        "name", "when", "register", "loop", "with_items", "vars", "tags",
        "become", "become_user", "delegate_to", "run_once", "ignore_errors",
        "changed_when", "failed_when", "no_log", "until", "retries", "delay",
        "block", "rescue", "always", "notify", "environment", "check_mode",
        "loop_control", "args",
    }
    found: set[str] = set()
    for root, _dirs, names in os.walk(PLAYBOOK_DIR):
        for name in sorted(names):
            if not name.endswith((".yml", ".yaml")):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                document = yaml.safe_load(handle) or []
            for play in document:
                for task in play.get("tasks", []) or []:
                    for key in task:
                        if key not in keywords and key.count(".") >= 2:
                            found.add(key)
    return found


def _resolves(module: str) -> bool:
    """Whether the image can resolve this module.

    Reads the output rather than the exit status: `ansible-doc` exits 0 while
    printing "was not found", so a returncode check reports every missing module
    as present -- which is exactly the mistake that makes this test worth
    writing carefully.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "ansible-doc", IMAGE, module],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = f"{result.stdout}\n{result.stderr}"
    return "was not found" not in output and "ERROR" not in output


def test_there_are_playbooks_to_check():
    """A walk that finds nothing would make everything below vacuously true."""
    assert _module_names(), "no modules found in any playbook -- check PLAYBOOK_DIR"


@pytest.mark.skipif(not _image_exists(), reason=BUILD_HINT)
def test_every_module_a_playbook_names_exists_in_the_image():
    missing = sorted(module for module in _module_names() if not _resolves(module))

    assert not missing, (
        "these modules are named by a catalogue playbook and do not exist in "
        f"{IMAGE}, so every job using them fails before reaching a device: "
        f"{missing}"
    )


@pytest.mark.skipif(not _image_exists(), reason=BUILD_HINT)
def test_the_check_would_have_caught_the_module_that_did_not_exist():
    """Guards the guard.

    If `_resolves` ever starts answering True for everything -- an entrypoint
    change, a docker flag, the exit-status mistake above -- this file would pass
    while checking nothing, and the next missing module reaches a device the
    same way the last one did.
    """
    assert not _resolves("ansible.netcommon.gather_facts")
    assert _resolves("ansible.builtin.debug")
