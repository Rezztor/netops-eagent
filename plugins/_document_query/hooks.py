from __future__ import annotations

import importlib.util
import threading

from helpers.print_style import PrintStyle


_LOCK = threading.Lock()
_CHECKED = False


def has_liteparse() -> bool:
    return importlib.util.find_spec("liteparse") is not None


def ensure_dependencies(raise_on_error: bool = True) -> bool:
    """Whether the parser this plugin needs is in the image. Never installs it.

    It used to: when `liteparse` could not be imported, this ran `uv pip
    install` against the package index and loaded the result into the web
    server. The product fetches nothing at runtime, and an installation on a
    closed network could not have reached the index anyway. The parser is a
    line in requirements.txt; an image without it is a broken build, and is
    reported as one.
    """
    global _CHECKED

    if _CHECKED and has_liteparse():
        return True

    with _LOCK:
        if has_liteparse():
            _CHECKED = True
            return True

        message = (
            "Document Query: the parser 'liteparse' is not in this image. It is listed in "
            "requirements.txt and is installed when the image is built; nothing is installed "
            "at runtime. Documents fall back to the other parsers until the image is rebuilt."
        )
        if raise_on_error:
            raise RuntimeError(message)
        PrintStyle.error(message)
        return False


def install() -> bool:
    return ensure_dependencies(raise_on_error=True)
