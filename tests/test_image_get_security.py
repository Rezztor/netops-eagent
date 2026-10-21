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


async def _request_image(path: str):
    app = Flask("test_image_get_security")
    handler = image_get.ImageGet(app, threading.Lock())
    with app.test_request_context("/api/image_get"):
        return await handler.process({"path": path}, request)


def test_image_get_serves_images_inside_base_dir(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    image_path = base_dir / "usr" / "uploads" / "safe.png"
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
    link_path = base_dir / "usr" / "uploads" / "linked.png"
    link_path.parent.mkdir(parents=True)
    link_path.symlink_to(outside_image)

    response = asyncio.run(_request_image(str(link_path)))

    assert response.status_code == 403


def test_image_get_hardens_svg_responses(tmp_path, monkeypatch):
    base_dir = tmp_path / "eagent"
    _patch_base_dir(monkeypatch, base_dir)
    svg_path = base_dir / "usr" / "uploads" / "payload.svg"
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
            return "/eagent/usr/uploads/remote.png"
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

    response = asyncio.run(_request_image("/eagent/usr/uploads/remote.png"))

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

    response = asyncio.run(_request_image("/eagent/usr/uploads/rejected.png"))

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
