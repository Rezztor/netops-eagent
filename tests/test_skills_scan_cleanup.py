"""An uploaded scan archive leaves nothing behind under tmp/ (audit 6, L5)."""

from __future__ import annotations

import io
import zipfile

from werkzeug.datastructures import FileStorage

from api.skills_scan import SkillsScan
from helpers import files


def test_scan_upload_removes_its_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "get_abs_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("pack/alpha/SKILL.md", "---\nname: alpha\ndescription: a\n---\n")
    buf.seek(0)

    result = object.__new__(SkillsScan)._prepare_uploaded_archive(
        FileStorage(stream=buf, filename="pack.zip")
    )

    assert result["success"] is True
    assert result["skills"] == [{"relative_path": "alpha"}]
    assert list((tmp_path / "tmp" / "skill_scans").iterdir()) == []
    assert list((tmp_path / "tmp" / "uploads").iterdir()) == []
