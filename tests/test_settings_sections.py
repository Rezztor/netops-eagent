"""The settings registry, and the documentation that points at it.

`frontend/src/routes/settings/registry.tsx` declares every settings section --
its id, which is also its route, and its title, which is what documentation
calls it. Both are contracts with things outside the file: `/settings/licence`
is printed in the customer install guide, and "Settings -> Licence" is how a
person is told to find it.

Before the registry there was nothing to check against. Sections were scroll
positions in a 1,199-line component, so a guide could name a screen that did not
exist and only a reader would find out. Three documents drifted that way in one
month -- the README, the customer install guide, and a delivery plan -- each
naming a plausible interface path that was not there.

These tests are structural on purpose. They pin the ids, the groups and the
names documentation uses, which is where drift actually happens.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import rbac  # noqa: E402

SETTINGS_DIR = PROJECT_ROOT / "frontend" / "src" / "routes" / "settings"
REGISTRY_TSX = SETTINGS_DIR / "registry.tsx"
TYPES_TS = SETTINGS_DIR / "types.ts"

pytestmark = pytest.mark.skipif(
    not REGISTRY_TSX.is_file(), reason="frontend scaffold not present"
)


def _registry_source() -> str:
    return REGISTRY_TSX.read_text(encoding="utf-8")


def _sections() -> list[dict[str, str]]:
    """Every entry in SECTIONS, as the handful of string fields worth pinning.

    Deliberately a regex over one array rather than anything that tries to
    understand TypeScript: the fields checked here are string literals written
    one per line, and a parser would be a great deal of machinery for that.
    """
    source = _registry_source()
    array = source[source.index("export const SECTIONS"):]
    entries: list[dict[str, str]] = []
    # Each entry opens at two-space indentation inside the array.
    for block in re.split(r"\n  \{\n", array)[1:]:
        block = block.split("\n  },")[0]
        fields = dict(re.findall(r'^\s{4}(id|title|group|summary): "([^"]+)"', block, re.M))
        if "id" in fields:
            entries.append(fields)
    return entries


def test_the_registry_is_not_empty():
    """A regex that silently matched nothing would make every test below pass."""
    assert len(_sections()) >= 8, "parsed too few sections; the regex has drifted"


def test_every_section_declares_the_fields_the_screen_renders():
    """A section missing one of these renders a blank index row or pane head."""
    for section in _sections():
        for field in ("id", "title", "group", "summary"):
            assert field in section, f"section {section.get('id', '?')} has no {field}"


def test_section_ids_are_unique():
    """The id is the route. Two sections sharing one makes the second unreachable."""
    ids = [section["id"] for section in _sections()]
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert not duplicates, f"duplicate section ids: {duplicates}"


def test_section_ids_are_url_safe():
    """They appear in `/settings/<id>` and in printed documentation."""
    for section in _sections():
        assert re.fullmatch(r"[a-z0-9-]+", section["id"]), (
            f"section id {section['id']!r} is not a clean URL segment"
        )


def test_every_group_used_is_declared():
    """The index renders group by group; an undeclared one is never drawn."""
    types_ts = TYPES_TS.read_text(encoding="utf-8")
    # Split on "= [" rather than "]": the type annotation itself ends in
    # `SectionGroup[]`, and splitting on a bare bracket ate the whole list and
    # left this test asserting nothing.
    body = types_ts.split("SECTION_GROUPS")[1].split("= [")[1].split("]")[0]
    declared = set(re.findall(r'"([A-Za-z]+)"', body))
    assert declared, "could not parse SECTION_GROUPS from types.ts"
    used = {section["group"] for section in _sections()}
    assert used <= declared, f"sections use undeclared groups: {sorted(used - declared)}"


def test_permissions_named_in_the_registry_exist():
    """A gate on a permission the backend does not have gates on nothing."""
    source = _registry_source()
    named = set(re.findall(r"PERMISSIONS\.([A-Za-z]+)", source))
    types_ts = (PROJECT_ROOT / "frontend" / "src" / "api" / "types.ts").read_text(
        encoding="utf-8"
    )
    block = types_ts[types_ts.index("export const PERMISSIONS"): types_ts.index("} as const;")]
    for key in named:
        match = re.search(rf'\b{key}:\s*"([^"]+)"', block)
        assert match, f"registry names PERMISSIONS.{key}, which the frontend does not declare"
        assert match.group(1) in rbac.ALL_PERMISSIONS, (
            f"registry gates on {match.group(1)!r}, which helpers/rbac.py does not define"
        )


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

# Where a reader is being told how to drive the interface as it is now.
# `docs/internal/research/` is excluded: those are plans and decision records,
# and they describe what was intended at a point in time rather than what is on
# screen today. Rewriting history to match the present would destroy the record.
DOC_ROOTS = ("README.md", "docs/guides", "docs/setup", "deploy")

# References inherited from the interface removed in ADR 0015, in documents
# that have not been revisited since. Each names a Settings tab that the React
# interface never had.
#
# This list may shrink and must never grow. It exists so the drift is recorded
# rather than silently tolerated, and so a *new* wrong path fails this test
# instead of joining them.
KNOWN_PRE_REACT_PATHS = {
    ("docs/guides/api-integration.md", "External Services"),
    # "Project Settings -> Secrets". There is no project-scoped settings screen
    # either, and no secrets screen anywhere -- caught by the same pattern, and
    # left in rather than excluded, because it is the same debt.
    ("docs/guides/api-integration.md", "Secrets"),
    ("docs/guides/model-presets.md", "Agent"),
    ("docs/setup/dev-setup.md", "Development"),
}

# "Settings -> X" or "Settings → X", with optional markdown bold around it.
DOC_PATH_RE = re.compile(r"Settings\s*(?:→|->)\s*\*{0,2}([A-Z][A-Za-z ]*?)\s*(?:\*|\n|,|\.|$|→|->)")


def _documented_paths() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in DOC_ROOTS:
        path = PROJECT_ROOT / root
        files = [path] if path.is_file() else sorted(path.rglob("*.md"))
        for doc in files:
            rel = doc.relative_to(PROJECT_ROOT).as_posix()
            for name in DOC_PATH_RE.findall(doc.read_text(encoding="utf-8")):
                found.add((rel, name.strip()))
    return found


def test_documented_settings_paths_name_a_real_section():
    """"Settings -> Licence" has to be somewhere a reader can actually click."""
    titles = {section["title"] for section in _sections()}
    unknown = {
        (doc, name)
        for doc, name in _documented_paths()
        if name not in titles and (doc, name) not in KNOWN_PRE_REACT_PATHS
    }
    assert not unknown, (
        "documentation names settings sections that do not exist: "
        + ", ".join(f"{doc}: 'Settings -> {name}'" for doc, name in sorted(unknown))
    )


def test_the_known_stale_paths_have_not_grown():
    """The allowlist is a record of debt, not a place to put new debt.

    A stale entry that has since been fixed should be deleted from the set, and
    this fails until it is -- otherwise the list outlives the problem and stops
    describing anything.
    """
    documented = _documented_paths()
    stale = KNOWN_PRE_REACT_PATHS - documented
    assert not stale, (
        "these are listed as known-stale but no longer appear; delete them from "
        f"KNOWN_PRE_REACT_PATHS: {sorted(stale)}"
    )


def test_the_customer_install_guide_points_at_a_real_section():
    """The one document a customer is handed. It names Settings -> Licence."""
    guide = PROJECT_ROOT / "deploy" / "customer" / "INSTALL.md"
    if not guide.is_file():
        pytest.skip("customer bundle not present")
    text = guide.read_text(encoding="utf-8")
    assert "Settings → Licence" in text
    assert "licence" in {section["id"] for section in _sections()}
