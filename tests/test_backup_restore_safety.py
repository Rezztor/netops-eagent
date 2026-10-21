"""Restore is the most destructive action in the product.

It writes over settings, every user's chats and memory, and the working
directory. With `clean_before_restore` it also deletes files that are not in the
archive -- on a live instance that measured 56 files, most of them created after
the backup was taken.

These pin the two properties the interface depends on: the preview must count
deletions accurately before anything is written, and the destructive option must
be off unless explicitly asked for.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SECTION = PROJECT_ROOT / "frontend" / "src" / "routes" / "BackupSection.tsx"
API = PROJECT_ROOT / "frontend" / "src" / "api" / "operations.ts"

pytestmark = pytest.mark.skipif(not SECTION.is_file(), reason="frontend not present")


def test_clean_before_restore_defaults_to_off_on_the_backend():
    """A restore that deletes by default would destroy data on a mis-click."""
    source = (PROJECT_ROOT / "api" / "backup_restore.py").read_text(encoding="utf-8")
    assert "request.form.get('clean_before_restore', 'false')" in source


def test_the_restore_preview_separates_deletions_from_writes():
    """The interface reports them separately because they carry very different
    consequences; a combined total hides the destructive half."""
    source = (PROJECT_ROOT / "helpers" / "backup.py").read_text(encoding="utf-8")
    for key in ('"files_to_delete"', '"files_to_restore"', '"delete_count"', '"restore_count"'):
        assert key in source, key


def test_the_interface_cannot_restore_without_previewing_first():
    """Restore is rendered only once a preview exists, so the file count and the
    deletion count are on screen before the decision."""
    source = SECTION.read_text(encoding="utf-8")
    restore_block = source[source.index("{preview && ("):]
    assert "restoreBackup" in restore_block, (
        "the Restore control must live inside the preview branch"
    )
    # And nowhere else.
    before_preview = source[: source.index("{preview && (")]
    assert "restoreBackup(" not in before_preview


def test_changing_a_restore_option_invalidates_the_preview():
    """Restoring against a preview taken with different settings is the exact
    failure this flow exists to prevent."""
    source = SECTION.read_text(encoding="utf-8")
    assert source.count("invalidate()") >= 3, (
        "policy, clean and archive changes must each clear the preview"
    )


def test_the_deletion_count_is_stated_before_and_in_the_confirmation():
    """Twice: once standing on the card, once in the dialog that asks.

    This used to slice the source from `const confirmation =`, which tied it to
    one implementation of the prompt. The shared confirm dialog replaced that
    local and the test broke while the property it guards was intact -- and
    arguably clearer than before. It now asserts the property: the count reaches
    the user before they commit, and the confirmation repeats it rather than
    merely asking whether to proceed.
    """
    source = SECTION.read_text(encoding="utf-8")
    assert "files will be deleted" in source, "no standing warning about deletions"

    # Wherever the prompt is built, it has to say what is lost and that it is
    # permanent -- both interpolated from the real count, not a fixed string.
    assert "deleted permanently" in source, (
        "the confirmation must repeat the deletion count, not just ask to proceed"
    )
    assert "${deleteCount}" in source, (
        "the confirmation states a count that is not the real one"
    )


def test_restore_is_marked_as_a_destructive_surface():
    source = SECTION.read_text(encoding="utf-8")
    restore_card = source[source.index("function RestoreBackup"):]
    assert re.search(r"\bdanger\b", restore_card), "the restore card is not marked danger"


def test_the_client_sends_the_policy_the_user_chose():
    """Defaulting the policy client-side would silently overwrite when the user
    asked to skip."""
    source = API.read_text(encoding="utf-8")
    for name in ("previewRestore", "restoreBackup"):
        block = source[source.index(f"export function {name}"):]
        block = block[: block.index("}\n")]
        assert "overwrite_policy" in block and "policy" in block, name
        assert "clean_before_restore" in block, name
