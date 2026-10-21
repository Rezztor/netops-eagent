"""What must never reach a customer.

The image is delivered to companies who buy this product. `.dockerignore`
keeps internal material out of it, but a `.dockerignore` entry is exactly
the kind of thing that gets quietly undone by a later "let's simplify the
ignore file" change, and nothing else would notice until a customer had
already received the result.

These tests check the *rules*, not a built image, so they run in a normal
suite with no Docker daemon. `test_customer_packaging_and_delivery` in
docs/internal/research/customer-packaging-and-delivery-plan.md §1 records
why each entry is on the list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"

# Path -> why it must not ship. The reason is part of the test: a future
# reader deciding whether to remove an entry needs to know what it was for.
MUST_NOT_SHIP = {
    "docs/internal/": (
        "the maintainers' decision records and roadmaps"
    ),
    "tests/": (
        "test names and fixtures describe internal behaviour and security assumptions"
    ),
    "scripts/sign_release.py": (
        "the release signing tool, Rezztor's side of ADR 0037; 'never shipped'"
    ),
    "CONTRIBUTING.md": "internal engineering process",
    "image/": (
        "licensed vendor lab images (Cisco, F5) for the owner's own test lab: not ours "
        "to hand to a customer, and 3 GB of them made a 10 GB ISO on 2026-10-11"
    ),
    "graphify-out/": (
        "a generated graph of the repository that quotes docs/internal/; "
        "41 MB of it shipped while docs/internal/ itself was excluded"
    ),
    "handoff/": "the maintainers' session notes",
    "obsidion-note/": "the maintainer's own notes",
    "docs/plans/": "build plans and roadmaps, the same material as docs/internal/",
    ".github/": "CI workflows; nothing in a running product reads them",
    ".hermes/": "an assistant's configuration for this repository",
    ".vscode/": "editor configuration",
    "docker/run/runner_key": "the development runner's SSH private key, present only on a build machine",
    "docker/run/runner_key.pub": "its public half; ships with nothing that needs it",
    "site/": (
        "the public marketing website, which lives in its own repository; a "
        "checkout or symlink at this path while working on both must not "
        "drag node_modules into a customer image"
    ),
}


# Patterns rather than paths, so kept apart from the re-include check below.
SECRETS_AT_ANY_DEPTH = {
    "**/*.env": (
        "environment files. `*.env` matched the root of the build context only, so the "
        "development stack's docker/run/.env went into an image built on that machine"
    ),
    "**/*.pem": "a key or certificate somebody left in the tree",
}


def test_a_uat_trust_file_in_the_working_folder_stays_out_of_the_build_context():
    """ADR 0085: the trust reaches a UAT image as a build argument only; a copy in the tree never rides along."""
    lines = _dockerignore_lines()
    assert {"**/uat-trust.json", "**/licence_trust.json"} <= set(lines)
    assert not any(line.startswith("!") and "trust" in line for line in lines)


# The owner's rule of 2026-10-12: a customer gets what the machine runs and
# nothing that explains, tests or builds it. Patterns, so kept apart too.
ENGINEERING_MATERIAL = {
    "**/*.dox.md": "210 file-level notes: a map of every module, better than the source for finding one's way",
    "**/AGENTS.md": "130 engineering contracts; the AGENTS.md the product reads is a customer's own, under usr/",
    "**/README.md": "plugin and folder notes that cite the decision records and the developer contracts",
    "CLAUDE.md": "the architecture of the whole product on one page",
    "docs/": "developer guides; the customer guides are handed over on their own, nothing in the product serves them",
    "**/tests/": "every plugin's and both executors' tests: `tests/` alone matched the root only",
    "**/conftest.py": "test fixtures",
    "testsupport.py": "test helpers",
    "pytest.ini": "the test configuration and its long comment",
    "requirements.dev.txt": "the development tools",
    "deploy/": "the customer stack's sources and the whole ISO builder; the machine runs the copies the bundle carries",
    "scripts/": "the scripts that build the images, the bundle and the ISO",
    "**/Dockerfile*": "how each image is built",
}

# Read by the agent at run time, and still a description of the internals:
# the owner decided on 2026-10-12 that these are not delivered.
NOT_DELIVERED_TO_THE_AGENT = (
    "skills/eagent-development/",
    "skills/eagent-create-plugin/",
    "skills/eagent-debug-plugin/",
    "skills/eagent-manage-plugin/",
    "skills/eagent-plugin-router/",
    "skills/eagent-review-plugin/",
    "knowledge/main/about/architecture.md",
)


def test_the_developer_skills_stay_here_and_no_delivered_file_points_at_one():
    lines = _dockerignore_lines()
    missing = [path for path in NOT_DELIVERED_TO_THE_AGENT if path not in lines]
    assert not missing, f"delivered to customers again: {missing}"
    held_back = [path.split("/")[1] for path in NOT_DELIVERED_TO_THE_AGENT if path.startswith("skills/")]
    pointing = []
    for delivered in [*PROJECT_ROOT.glob("skills/*/SKILL.md"), *PROJECT_ROOT.glob("plugins/*/skills/**/SKILL.md"),
                      *PROJECT_ROOT.glob("prompts/*.md"), *PROJECT_ROOT.glob("knowledge/**/*.md")]:
        if delivered.parent.name in held_back or delivered.name in ("AGENTS.md", "architecture.md"):
            continue
        text = delivered.read_text(encoding="utf-8")
        pointing += [f"{delivered.relative_to(PROJECT_ROOT)} -> {name}" for name in held_back if name in text]
    assert not pointing, f"a delivered file sends the agent to a skill the customer's image does not have: {pointing}"


@pytest.mark.parametrize("pattern,reason", sorted(ENGINEERING_MATERIAL.items()))
def test_engineering_material_is_excluded_from_the_image(pattern: str, reason: str):
    assert pattern in _dockerignore_lines(), f"'{pattern}' is no longer excluded from the customer image: {reason}"


def test_the_build_removes_its_own_inputs_and_the_bundle_checks_the_images():
    """What the build needs cannot be in .dockerignore, so the assemble step
    removes it; and the bundle script is the one place that looks at a real
    image, where an undone rule is found before a customer has it."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder = dockerfile[: dockerfile.rindex("\nFROM ")]
    assert "find /eagent/frontend -mindepth 1 -maxdepth 1 ! -name dist -exec rm -rf {} +" in builder
    assert "rm -rf /eagent/docker" in builder
    assert "find /eagent -name 'requirements*.txt' -delete" in builder
    bundle = (PROJECT_ROOT / "scripts" / "build_offline_bundle.sh").read_text(encoding="utf-8")
    check = bundle[bundle.index('for image in "$APP" "$ANSIBLE" "$CLOUD"'): bundle.index('rm -rf "$BUNDLE"')]
    for name in ('"*.dox.md"', "AGENTS.md", "README.md", "-type d -name tests", "/eagent/frontend/src", "/eagent/docker",
                 "/eagent/skills/eagent-development", "/eagent/knowledge/main/about/architecture.md"):
        assert name in check, f"the bundle no longer looks for {name} in the images"
    assert "exit 1" in check


def _dockerignore_lines() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@pytest.mark.parametrize("path,reason", sorted(MUST_NOT_SHIP.items()))
def test_internal_material_is_excluded_from_the_image(path: str, reason: str):
    """Each entry is in .dockerignore, so it never enters the build context."""
    lines = _dockerignore_lines()
    assert path in lines, (
        f"'{path}' is no longer excluded from the customer image.\n"
        f"Why it was excluded: {reason}"
    )


@pytest.mark.parametrize("pattern,reason", sorted(SECRETS_AT_ANY_DEPTH.items()))
def test_secrets_are_excluded_at_any_depth(pattern: str, reason: str):
    """A .dockerignore pattern with no `**/` matches the root only."""
    lines = _dockerignore_lines()
    assert pattern in lines, f"'{pattern}' is no longer excluded: {reason}"
    assert not any(line.startswith("!") and line.endswith((".env", ".pem")) for line in lines)


def test_no_re_include_undoes_those_exclusions():
    """A `!pattern` line later in the file overrides an earlier exclusion.

    The file already uses re-includes (`!**/.gitkeep`), so this is a live
    footgun rather than a hypothetical one.
    """
    reincludes = [line for line in _dockerignore_lines() if line.startswith("!")]
    for pattern in reincludes:
        body = pattern[1:].strip().rstrip("/")
        for excluded in MUST_NOT_SHIP:
            excluded_root = excluded.rstrip("/").split("/")[0]
            assert not body.startswith(excluded_root), (
                f"re-include '{pattern}' would pull back excluded '{excluded}'"
            )


class TestNoLgplDependency:
    """paramiko was the one LGPL dependency in an otherwise permissive tree.
    It was dropped in favour of the OpenSSH subprocess transport that had
    already replaced it everywhere (ADR 0018). These stop it returning by
    accident -- reintroducing it revives a licence obligation that currently
    does not exist."""

    def test_nothing_imports_paramiko(self):
        importers = []
        for path in PROJECT_ROOT.rglob("*.py"):
            parts = path.relative_to(PROJECT_ROOT).parts
            # usr/ and tmp/ are untracked runtime state: gitignored (`usr/**/*`)
            # and excluded from the image (`usr/*` in .dockerignore). Nothing
            # there is delivered, so nothing there can revive a licence
            # obligation -- and scanning it failed this test for any developer
            # who kept a lab script beside the application.
            if parts[0] in {".venv", "usr", "tmp"} or "node_modules" in parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(import|from)\s+paramiko", text, re.MULTILINE):
                importers.append(str(path.relative_to(PROJECT_ROOT)))
        assert not importers, (
            "paramiko is LGPL and was deliberately removed; reintroducing it "
            f"revives that obligation. Imported by: {importers}"
        )

    def test_paramiko_is_not_a_declared_dependency(self):
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert not re.search(r"^paramiko\b", requirements, re.MULTILINE), (
            "paramiko is back in requirements.txt"
        )


def test_directly_imported_packages_are_declared_dependencies():
    """A package the product imports must be in requirements.txt, not merely
    installed because something else happened to pull it in.

    `bcrypt` was exactly this: `helpers/users.py` hashes every account
    password with it, but it was never declared and arrived only as a
    paramiko dependency. Dropping paramiko therefore broke authentication --
    a failure that appeared only when a container was actually built and
    booted, because every development environment already had it installed.
    """
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    declared = {
        re.split(r"[=<>!\[; ]", line.strip(), 1)[0].lower()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    # Third-party packages imported by shipped code whose absence is not
    # obvious from the import name alone. Extend when a new one is added.
    load_bearing = {
        "bcrypt": "helpers/users.py -- password hashing for every account",
        "cryptography": "rezlic/formats.py -- licence signature verification",
        "flask": "the web application itself",
    }
    missing = {
        package: why
        for package, why in load_bearing.items()
        if package not in declared
    }
    assert not missing, (
        "imported but not declared in requirements.txt, so only present while "
        f"some other package happens to depend on it: {missing}"
    )


class TestMultiStageBuild:
    """The source tree must not survive in any layer of the delivered image.

    A single-stage build cannot achieve this: `COPY ./ /git/eagent` writes the
    tree into a layer, and deleting it later only hides it from the running
    container -- `docker save` still recovers it. These check the Dockerfile's
    structure, since building an image is not something a unit test should do;
    the property itself is verified by inspecting a built image at release
    time (customer-packaging-and-delivery-plan.md §2).
    """

    @staticmethod
    def _dockerfile() -> str:
        return (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_the_build_has_a_discarded_builder_stage(self):
        text = self._dockerfile()
        assert "AS builder" in text, "the build is single-stage again"
        assert text.count("\nFROM ") >= 1 and "FROM rezztor/eagent-base:latest\n" in text, (
            "the runtime stage must start from the base image, not from builder"
        )

    def test_the_source_tree_is_only_copied_in_the_builder_stage(self):
        """`COPY ./ /git/eagent` is the line that creates the recoverable
        layer. It may appear only before the runtime stage begins."""
        text = self._dockerfile()
        runtime_stage_at = text.rindex("\nFROM ")
        runtime_stage = text[runtime_stage_at:]
        assert "/git/eagent" not in runtime_stage, (
            "the runtime stage copies the source tree in, which is exactly "
            "what the multi-stage split exists to prevent"
        )
        assert "COPY ./ /git/eagent" in text[:runtime_stage_at], (
            "the builder stage no longer copies the source; the build would "
            "have nothing to build from"
        )

    def test_the_runtime_takes_only_build_output_from_the_builder(self):
        """What crosses the stage boundary is the property worth pinning: a
        built virtualenv and the assembled application, never the source
        tree or the build context."""
        text = self._dockerfile()
        copies = re.findall(
            r"^COPY --from=builder (?:--\S+ )*(\S+) (\S+)", text, re.MULTILINE
        )
        assert copies, "nothing is copied from the builder stage"
        # /opt/models is the embedding model the build downloads from
        # huggingface.co: build output, no application source in it, and the
        # reason an air-gapped install can embed anything at all.
        allowed = {"/opt/venv-eagent", "/eagent", "/opt/models"}
        unexpected = [src for src, _ in copies if src not in allowed]
        assert not unexpected, (
            f"unexpected paths copied out of the builder: {unexpected}. "
            "Anything new here ships to customers -- confirm it is build "
            "output, not build input."
        )

    def test_local_developer_artifacts_are_not_in_the_build_context(self):
        """deploy/dev/.bin holds pinned tooling a developer downloads locally.
        It is gitignored, which does not reach Docker: 125 MB of one machine's
        binaries were shipping in every customer image."""
        assert "deploy/dev/.bin/" in _dockerignore_lines(), (
            "local developer binaries are back in the build context"
        )


def test_no_private_key_is_committed():
    """A committed signing key would let anyone forge licences -- the one
    failure the whole scheme cannot survive.

    Checks git's tracked files rather than the working tree: an operator's
    own `docker/run/runner_key` is generated locally and gitignored by
    design (deployment.md tells them to create it), so its presence on
    disk is correct and only its *commitment* would be a problem.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")

    offenders = []
    for raw in tracked:
        if not raw:
            continue
        path = PROJECT_ROOT / raw.decode()
        if not path.is_file():
            continue
        try:
            head = path.read_bytes()[:4096]
        except OSError:
            continue
        if b"BEGIN PRIVATE KEY" in head or b"BEGIN OPENSSH PRIVATE KEY" in head:
            offenders.append(raw.decode())
    assert not offenders, f"private key material is committed: {offenders}"


def test_agent_worktrees_stay_out_of_the_build_context():
    """.claude/worktrees/ holds whole checkouts; a local build copied them into the image (UAT run, 2026-10-19)."""
    assert ".claude/" in _dockerignore_lines()
