"""One account cannot fill the volume every account shares: uploads honour the
per-file limit and the per-account quota, and an extraction that would go over
the quota is removed."""

import io
from pathlib import Path
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from api.extract_work_dir_archive import extract_archive
from helpers import file_browser
from helpers.file_browser import FileBrowser


@pytest.fixture(autouse=True)
def rooted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    original = FileBrowser.__init__
    monkeypatch.setattr(
        FileBrowser, "__init__", lambda self, base_dir=None: original(self, base_dir=base_dir or tmp_path)
    )
    return tmp_path


def upload(name: str, size: int) -> FileStorage:
    return FileStorage(stream=io.BytesIO(b"x" * size), filename=name)


def test_save_files_applies_the_per_file_limit(rooted: Path, monkeypatch) -> None:
    monkeypatch.setattr(FileBrowser, "MAX_FILE_SIZE", 10)

    saved, failed = FileBrowser().save_files([upload("small.txt", 10), upload("big.bin", 11)])

    assert (saved, failed) == (["small.txt"], ["big.bin"])
    assert not (rooted / "big.bin").exists()


def test_save_files_stops_at_the_account_quota(rooted: Path, monkeypatch) -> None:
    monkeypatch.setattr(file_browser, "ACCOUNT_QUOTA_BYTES", 25)
    (rooted / "already.txt").write_bytes(b"x" * 10)

    saved, failed = FileBrowser().save_files([upload("a.txt", 10), upload("b.txt", 10)])

    assert (saved, failed) == (["a.txt"], ["b.txt"])


def test_an_extraction_over_the_quota_is_removed(rooted: Path, monkeypatch) -> None:
    monkeypatch.setattr(file_browser, "ACCOUNT_QUOTA_BYTES", 1000)
    with zipfile.ZipFile(rooted / "big.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("zeros.txt", "0" * 5000)

    with pytest.raises(file_browser.QuotaExceeded):
        extract_archive("/big.zip")

    assert not (rooted / "big").exists()


# --- chat attachments go to the same uploads folder, under the same quota ------


@pytest.fixture
def scoped_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from helpers import files, user_scope

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(file_browser, "ACCOUNT_QUOTA_BYTES", 25)
    with user_scope.user_scope("asha"):
        yield Path(user_scope.get_user_path(user_scope.UPLOADS_DIR))


@pytest.mark.asyncio
async def test_a_chat_attachment_over_the_quota_is_refused_with_413(scoped_uploads: Path) -> None:
    import threading

    from flask import Flask

    from api.message import Message

    app = Flask(__name__)
    data = {"text": "hi", "attachments": [(io.BytesIO(b"x" * 20), "a.txt"), (io.BytesIO(b"x" * 20), "b.txt")]}
    handler = Message(app=app, thread_lock=threading.RLock())  # type: ignore[arg-type]
    with app.test_request_context("/message", method="POST", data=data, content_type="multipart/form-data"):
        from flask import request

        response = await handler.handle_request(request)  # type: ignore[arg-type]

    assert response.status_code == 413
    assert "quota" in response.get_data(as_text=True)
    assert not scoped_uploads.exists() or not any(scoped_uploads.iterdir()), "nothing is half-written"


@pytest.mark.asyncio
async def test_an_api_message_attachment_over_the_quota_is_refused(scoped_uploads: Path) -> None:
    import base64
    import threading

    from api.api_message import ApiMessage

    handler = ApiMessage(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    payload = {
        "message": "hi",
        "attachments": [
            {"filename": "a.txt", "base64": base64.b64encode(b"x" * 20).decode()},
            {"filename": "b.txt", "base64": base64.b64encode(b"x" * 20).decode()},
        ],
    }
    with pytest.raises(file_browser.QuotaExceeded):
        await handler.process(payload, request=None)  # type: ignore[arg-type]

    assert sorted(p.name for p in scoped_uploads.iterdir()) == ["a.txt"]


def test_concurrent_uploads_cannot_each_pass_the_check(scoped_uploads: Path) -> None:
    """Every request that checked before any of them wrote used to be let in,
    so N parallel uploads could land N times the room that was left."""
    import asyncio
    import threading
    import time
    from types import SimpleNamespace

    from werkzeug.datastructures import MultiDict

    from api.upload import UploadFile
    from helpers import user_scope

    class SlowUpload(FileStorage):
        def save(self, dst, *args, **kwargs):
            time.sleep(0.2)
            super().save(dst, *args, **kwargs)

    handler = UploadFile(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    start = threading.Barrier(4)
    outcomes: list[str] = []

    def one(n: int) -> None:
        upload = SlowUpload(stream=io.BytesIO(b"x" * 20), filename=f"f{n}.txt")
        request = SimpleNamespace(files=MultiDict([("file", upload)]))
        with user_scope.user_scope("asha"):
            start.wait()
            try:
                asyncio.run(handler.process({}, request))  # type: ignore[arg-type]
                outcomes.append("saved")
            except file_browser.QuotaExceeded:
                outcomes.append("refused")

    threads = [threading.Thread(target=one, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["refused", "refused", "refused", "saved"]
    assert len(list(scoped_uploads.iterdir())) == 1
