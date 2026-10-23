import asyncio
import base64
import sys
import threading
from pathlib import Path

from flask import Flask, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api import image_get


def _patch_base_dir(monkeypatch, base_dir: Path, *, development: bool = False) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)

    def fake_get_abs_path(*parts: str) -> str:
        if len(parts) == 1 and Path(str(parts[0])).is_absolute():
            return str(Path(str(parts[0])))
        return str(base_dir.joinpath(*(str(part) for part in parts)))

    monkeypatch.setattr(image_get.files, "get_base_dir", lambda: str(base_dir))
    monkeypatch.setattr(image_get.files, "get_abs_path", fake_get_abs_path)
    monkeypatch.setattr(image_get.runtime, "is_development", lambda: development)
    # Signed in as alice, with the default working-folder base, unless a test says otherwise.
    monkeypatch.setattr(image_get.user_scope, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(image_get.workdir, "get_workdir_base", lambda: str(base_dir / "usr" / "workdir"))


async def _request_image(path: str):
    app = Flask("test_image_get_security")
    handler = image_get.ImageGet(app, threading.Lock())
    with app.test_request_context("/api/image_get"):
        return await handler.process({"path": path}, request)


def test_image_get_serves_images_inside_base_dir(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    image_path = base_dir / "usr" / "users" / "alice" / "uploads" / "safe.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    response = asyncio.run(_request_image(str(image_path)))

    assert response.status_code == 200
    assert response.headers["X-File-Type"] == "image"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_image_get_blocks_image_paths_outside_base_dir(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(b"outside")

    response = asyncio.run(_request_image(str(outside_image)))

    assert response.status_code == 403
    assert response.get_data(as_text=True) == "Path is outside of allowed directory"


def test_image_get_blocks_symlink_escape_from_base_dir(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    outside_image = tmp_path / "secret.png"
    outside_image.write_bytes(b"secret")
    link_path = base_dir / "usr" / "users" / "alice" / "uploads" / "linked.png"
    link_path.parent.mkdir(parents=True)
    link_path.symlink_to(outside_image)

    response = asyncio.run(_request_image(str(link_path)))

    assert response.status_code == 403


def test_image_get_hardens_svg_responses(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    svg_path = base_dir / "usr" / "users" / "alice" / "uploads" / "payload.svg"
    svg_path.parent.mkdir(parents=True)
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )

    response = asyncio.run(_request_image(str(svg_path)))

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"].startswith("sandbox;")
    assert "script-src 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_image_get_development_fallback_validates_remote_path(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir, development=True)
    calls = []

    async def fake_call_development_function(func, *args, **kwargs):
        calls.append(func.__name__)
        if func is image_get._resolve_allowed_image_path:
            return "/eagent/usr/users/alice/uploads/remote.png"
        if func is image_get.files.exists:
            return True
        if func is image_get.files.read_file_base64:
            return base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        raise AssertionError(f"Unexpected remote call: {func.__name__}")

    monkeypatch.setattr(
        image_get.runtime,
        "call_development_function",
        fake_call_development_function,
    )

    response = asyncio.run(_request_image("/eagent/usr/users/alice/uploads/remote.png"))

    assert response.status_code == 200
    assert response.headers["X-File-Type"] == "image"
    assert calls == ["_resolve_allowed_image_path", "exists", "read_file_base64"]


def test_image_get_development_fallback_does_not_read_rejected_remote_path(
    tmp_path,
    monkeypatch,
):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir, development=True)
    calls = []

    async def fake_call_development_function(func, *args, **kwargs):
        calls.append(func.__name__)
        if func is image_get._resolve_allowed_image_path:
            raise ValueError("Path is outside of allowed directory")
        raise AssertionError(f"Unexpected remote call after validation: {func.__name__}")

    monkeypatch.setattr(
        image_get.runtime,
        "call_development_function",
        fake_call_development_function,
    )
    monkeypatch.setattr(
        image_get,
        "_send_fallback_icon",
        lambda _icon_name: image_get.Response("fallback", status=200),
    )

    response = asyncio.run(_request_image("/eagent/usr/users/alice/uploads/rejected.png"))

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "fallback"
    assert calls == ["_resolve_allowed_image_path"]


def test_image_get_blocks_another_accounts_folder(tmp_path, monkeypatch):
    """The base directory holds every account's files, so being inside it does
    not make an image the caller's to read."""
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    monkeypatch.setattr(image_get.user_scope, "get_current_user_id", lambda: "alice")
    for owner in ("alice", "bob"):
        image = base_dir / "usr" / "users" / owner / "uploads" / "shot.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

    mine = asyncio.run(_request_image(str(base_dir / "usr/users/alice/uploads/shot.png")))
    theirs = asyncio.run(_request_image(str(base_dir / "usr/users/bob/uploads/shot.png")))

    assert mine.status_code == 200
    assert theirs.status_code == 403


def test_image_get_blocks_another_accounts_working_folder(tmp_path, monkeypatch):
    """The working folder is `usr/workdir/<account>/`, beside `usr/users/` and not
    under it, so the rule for `usr/users/` did not cover it: an image an agent
    made for one account was served to any other that named the path."""
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    monkeypatch.setattr(image_get.user_scope, "get_current_user_id", lambda: "alice")
    for folder in ("alice", "bob", "documents"):
        image = base_dir / "usr" / "workdir" / folder / "scan.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

    mine = asyncio.run(_request_image(str(base_dir / "usr/workdir/alice/scan.png")))
    theirs = asyncio.run(_request_image(str(base_dir / "usr/workdir/bob/scan.png")))
    shared = asyncio.run(_request_image(str(base_dir / "usr/workdir/documents/scan.png")))

    assert mine.status_code == 200
    assert theirs.status_code == 403
    assert shared.status_code == 200, "the shared documents folder is everybody's (ADR 0034)"
    # One account's image must not be kept by a cache between the browser and the server.
    assert mine.headers["Cache-Control"].startswith("private")


def _image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def test_image_get_blocks_images_elsewhere_in_the_application_tree(tmp_path, monkeypatch):
    """Inside the base directory is not the caller's: a path in none of the
    per-account roots used to be served as it was, so any signed-in account
    could read any image in the application."""
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    for elsewhere in (
        "webui/public/logo.png",
        "usr/stray.png",
        "usr/uploads/legacy.png",
        "usr/projects/demo/shot.png",
        "tmp/chats/abc/shot.png",
    ):
        image = _image(base_dir / elsewhere)
        response = asyncio.run(_request_image(str(image)))
        assert response.status_code == 403, elsewhere
        assert response.get_data(as_text=True) == "Path is outside of allowed directory"


def test_image_get_blocks_everything_per_account_without_a_signed_in_owner(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    image = _image(base_dir / "usr" / "users" / "alice" / "shot.png")
    own_workdir = _image(base_dir / "usr" / "workdir" / "alice" / "p.png")
    monkeypatch.setattr(image_get.user_scope, "get_current_user_id", lambda: None)

    assert asyncio.run(_request_image(str(image))).status_code == 403
    assert asyncio.run(_request_image(str(own_workdir))).status_code == 403


def test_image_get_serves_the_callers_chat_images_and_screenshots(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    chat = base_dir / "usr" / "users" / "alice" / "chats" / "ctx1"
    mine = [_image(chat / "images" / "vision-load" / "a.png"), _image(chat / "screenshots" / "browser" / "b.png")]
    other = _image(base_dir / "usr" / "users" / "bob" / "chats" / "ctx2" / "screenshots" / "c.png")

    assert [asyncio.run(_request_image(str(p))).status_code for p in mine] == [200, 200]
    assert asyncio.run(_request_image(str(other))).status_code == 403


def test_image_get_serves_the_shared_documents_folder_and_the_callers_workdir(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    shared = _image(base_dir / "usr" / "workdir" / "documents" / "diagram.png")
    mine = _image(base_dir / "usr" / "workdir" / "alice" / "plots" / "p.png")
    theirs = _image(base_dir / "usr" / "workdir" / "bob" / "p.png")

    assert asyncio.run(_request_image(str(shared))).status_code == 200
    assert asyncio.run(_request_image(str(mine))).status_code == 200
    assert asyncio.run(_request_image(str(theirs))).status_code == 403


def test_image_get_serves_what_the_files_screen_lists(tmp_path, monkeypatch):
    """The Files screen asks for a name relative to the caller's own working
    folder, not an absolute path."""
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    _image(base_dir / "usr" / "workdir" / "alice" / "plots" / "p.png")
    _image(base_dir / "usr" / "workdir" / "bob" / "plots" / "q.png")

    mine = asyncio.run(_request_image("plots/p.png"))
    assert mine.status_code == 200
    assert mine.headers["X-File-Type"] == "image"
    # Not a way into somebody else's folder, nor out of the roots.
    assert asyncio.run(_request_image("../bob/plots/q.png")).status_code == 403
    assert asyncio.run(_request_image("../../../webui/public/logo.png")).status_code == 403
