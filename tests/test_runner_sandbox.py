"""What the code-execution runner must be, asserted rather than assumed.

Phase 6 moves agent-written code out of the application container into a runner
pod. ADR 0006 refused to ship that as untested YAML, and this file is the
difference: the settings below were established by building the image, running
it under Docker with each equivalent restriction applied, and attacking it from
the account agent code actually gets. ADR 0019 records the results.

These are static assertions on the manifests and the Dockerfile. They cannot
prove the pod is safe -- only a cluster can do that, and the NetworkPolicy in
particular is enforced by a CNI that is not present here. What they do prevent
is the specific way this regresses: someone adds a capability back, or drops
`readOnlyRootFilesystem` to fix a write error, and nothing notices.

The `docker` test at the end is the real one. It is skipped unless the image has
already been built, because building it takes minutes and a test suite that
takes minutes stops being run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "docker" / "runner" / "Dockerfile"
ENTRYPOINT = REPO / "docker" / "runner" / "entrypoint.sh"
IMAGE = "eagent-runner:hardened"

























# ── the image ────────────────────────────────────────────────────────


def test_the_image_carries_the_interpreters_the_tool_actually_invokes():
    """The failure this prevents was real and silent.

    `code_execution_tool` runs `ipython -c <code>` and
    `node /exe/node_eval.js <code>` -- not `python3`, not a bare `node`. Both
    come from the *application* image, so a runner built the obvious way starts
    cleanly, accepts SSH, and fails on the first line of agent code with
    `ipython: command not found`.
    """
    body = DOCKERFILE.read_text(encoding="utf-8")
    assert "ipython" in body
    assert "node_eval.js" in body, "the node evaluator the tool shells out to is missing"
    assert "npm" in body

    tool = (REPO / "plugins" / "_code_execution" / "tools" / "code_execution_tool.py").read_text()
    assert "ipython -c" in tool and "/exe/node_eval.js" in tool, (
        "the tool's runtime commands changed; the runner image must follow"
    )


def test_the_venv_is_symlinked_onto_the_default_path():
    """ENV PATH does not reach a command arriving over SSH.

    That shell is non-interactive and non-login: it sources no profile and takes
    its PATH from sshd's default, not from the image's ENV. Setting ENV PATH
    looks correct, builds correctly, and leaves `ipython: command not found`.
    """
    body = DOCKERFILE.read_text(encoding="utf-8")
    assert "/usr/local/bin/ipython" in body


def test_the_image_drops_privilege_and_strips_setuid():
    body = DOCKERFILE.read_text(encoding="utf-8")
    # No USER line, and that is deliberate rather than an omission: sshd has to
    # start as root to setuid into a slot account, because only root may. The
    # privilege that matters is the session's, and a session is an ordinary
    # unprivileged account -- verified live, CapEff is all zeroes. What keeps
    # this root small is the capability set in the compose files (SETUID,
    # SETGID, SYS_CHROOT, CHOWN, AUDIT_WRITE and nothing else) plus a read-only
    # root filesystem.
    assert re.search(r"^USER ", body, re.M) is None, (
        "a USER line would stop sshd switching into a slot account"
    )
    assert "only root may setuid into a slot" in body, (
        "the reason for running as root must stay next to the decision"
    )
    assert "perm -4000" in body, "setuid bits are not stripped"
    assert "PasswordAuthentication no" in body
    assert "PermitRootLogin no" in body


def test_the_entrypoint_refuses_to_start_unsafely():
    """Two refusals, both making a misconfiguration loud at rollout instead of
    silent until the first code execution."""
    body = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'if [ "$(id -u)" = "0" ]' in body, "it would start as root"
    assert "RUNNER_AUTHORIZED_KEY" in body, "it would start with nobody able to reach it"


def test_an_identity_file_selects_the_transport_that_can_reach_the_runner():
    """The hardened runner accepts public keys only, so paramiko's password
    session cannot reach it. Configuring a key must pick the OpenSSH transport,
    not merely be ignored."""
    tool = (REPO / "plugins" / "_code_execution" / "tools" / "code_execution_tool.py").read_text()
    assert "REZZTOR_RUNNER_IDENTITY_FILE" in tool
    assert "OpenSSHInteractiveSession" in tool


# ── the real one ─────────────────────────────────────────────────────


def _image_exists() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, text=True
    )
    return result.returncode == 0


@pytest.mark.skipif(
    not _image_exists(),
    reason=f"build it first: docker build -f docker/runner/Dockerfile -t {IMAGE} .",
)
def test_the_built_image_actually_refuses_to_run_as_root():
    """Against the real image, not the Dockerfile.

    Everything else here reads a file and believes it. This starts the thing and
    checks the refusal fires -- the one assertion in this file that could catch
    a Dockerfile that says the right words and produces the wrong image.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "0:0", "-e", "RUNNER_AUTHORIZED_KEY=x", IMAGE],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode != 0
    assert "must not run as root" in (result.stderr + result.stdout)


# ── findings from a real cluster ─────────────────────────────────────
#
# The tests below exist because each one failed, or would have failed, on a kind
# cluster running Calico. None of them could be found by reading a manifest, and
# none was caught by the Docker harness. ADR 0019 records the runs.


def test_the_entrypoint_does_not_chmod_the_runtime_volume():
    """It does not always own that directory.

    A volume can arrive with *group* ownership -- root:1000 -- so a process
    running as uid 1000 can write into it and cannot chmod it. Docker's
    `--tmpfs ...,uid=1000` does grant user ownership, so a chmod passes on this
    stack and fails with "Operation not permitted" anywhere it does not. That
    is exactly what happened on first deploy.
    """
    body = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'chmod 700 "${RUNTIME_DIR}"' not in body
    assert 'chmod' in body, "the files themselves still need restrictive modes"
    # The files it does own must still be locked down.
    assert 'chmod 0644 "${RUNTIME_DIR}/keys/${RUNNER_USER}/authorized_keys"' in body






def test_the_slot_pool_gives_every_user_a_real_account():
    """Per-user separation on a shared runner is UIDs, not a view.

    A slot is a Linux account with its own uid and a 0700 home, so one user's
    files are unreadable to another whatever either does to their own session.
    Verified live on 2026-09-18: alice's key is refused on bob's slot with
    `Permission denied (publickey)`, and from inside her own session bob's
    directory cannot be read, listed or written.
    """
    body = DOCKERFILE.read_text(encoding="utf-8")

    assert "RUNNER_SLOTS=" in body, "the pool size has to be pinned in the image"
    # Homes under WORKSPACE so Docker seeds the named volume from the image,
    # ownership included -- which is why the container never needs CAP_CHOWN.
    assert '--home-dir "${WORKSPACE}/${slot}"' in body
    assert 'install -d -m 0700' in body, "a slot home open to others is not a boundary"


def test_slot_accounts_have_no_password_rather_than_a_locked_one():
    """`useradd` leaves `!`, and OpenSSH reads that as a locked account.

    It only goes unnoticed today because a non-root sshd cannot read
    /etc/shadow, so the check passes vacuously. Running as root for the slot
    pool, `!` refuses every login with "account is locked" -- which says nothing
    about keys and sends you looking in the wrong place. `*` is an equally
    invalid hash, so password authentication stays impossible; it simply is not
    the locked marker.
    """
    body = DOCKERFILE.read_text(encoding="utf-8")
    assert body.count("--password '*'") >= 2, (
        "both the shared account and the slot accounts need an unlocked-but-"
        "passwordless shadow entry"
    )


def test_the_compose_files_grant_sshd_what_a_pty_login_needs():
    """Four capabilities, and two of them were missing.

    A session begins with sshd creating a pty, chowning it to the session's own
    uid, and writing a record to the kernel audit log. Without CAP_CHOWN the
    first fails; without CAP_AUDIT_WRITE the second does; either way sshd tears
    the session down before a prompt arrives and the client sees only
    "Connection closed by remote host". `ssh host command` keeps working
    throughout, because it needs no pty -- which is why nothing caught it until
    the stack was run and a real session attempted. The code execution tool
    opens a pty and nothing else in the product does, so this was the whole
    feature, silently.

    Not a longer list than needed: DAC_OVERRIDE stays dropped, so this root
    still cannot read a slot's 0700 home, and a session's own capability set is
    empty either way.
    """
    import re

    needed = {"SETUID", "SETGID", "SYS_CHROOT", "CHOWN", "AUDIT_WRITE"}
    for parts in (
        ("deploy", "customer", "docker-compose.yml"),
        ("docker", "run", "docker-compose.yml"),
    ):
        where = "/".join(parts)
        body = (REPO.joinpath(*parts)).read_text(encoding="utf-8")
        runner = re.split(r"(?m)^  (?=\S)", body)
        block = next(b for b in runner if b.startswith("runner:"))
        granted = set(re.findall(r"(?m)^      - ([A-Z_]+)$", block))
        assert "ALL" in re.findall(r"(?m)^      - ([A-Z_]+)$", block.split("cap_add")[0]), (
            f"{where}: the runner no longer drops ALL first"
        )
        missing = sorted(needed - granted)
        assert not missing, f"{where}: the runner cannot open a pty session without {missing}"
        extra = sorted(granted - needed - {"ALL"})
        assert not extra, f"{where}: the runner is granted more than a pty login needs: {extra}"
