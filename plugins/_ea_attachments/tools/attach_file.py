"""Attach a file to the reply, so the user can download it.

Deliberately a tool the agent calls rather than something that watches for
written files and guesses. A task may touch a dozen intermediate files; only the
agent knows which one was the point, and attaching the rest would be noise the
user has to sort through.
"""

from __future__ import annotations

import os
from pathlib import Path

from helpers import plugins as plugin_helpers, user_scope, workdir
from helpers.parallel_tools import user_facing_context
from helpers.tool import Response, Tool

from plugins._ea_attachments.helpers import runner, store

PLUGIN_NAME = "_ea_attachments"

_FALLBACK = {"max_file_mb": 25, "max_files_per_user": 200}


def _config() -> dict:
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME) or {}
    merged = {**_FALLBACK, **stored}
    for key, default in _FALLBACK.items():
        try:
            value = int(merged.get(key))
            merged[key] = value if value > 0 else default
        except (TypeError, ValueError):
            merged[key] = default
    return merged


class AttachFile(Tool):
    async def execute(self, path: str = "", name: str = "", **kwargs) -> Response:
        path = str(path or self.args.get("path") or "").strip()
        if not path:
            return Response(message="attach_file needs a `path`.", break_loop=False)

        owner = user_scope.get_current_user_id()
        if not user_scope.is_safe_user_id(owner):
            # Without an owner there is no directory to put it in and no one to
            # show it to. Better to say so than to write it somewhere shared.
            return Response(
                message="Cannot attach a file outside a signed-in conversation.",
                break_loop=False,
            )

        settings = _config()
        max_bytes = settings["max_file_mb"] * 1_048_576

        try:
            content = await self._read(path, owner, max_bytes)
        except runner.FetchError as error:
            return Response(message=f"Could not attach {path}: {error}", break_loop=False)
        except Exception as error:  # noqa: BLE001 - reported to the agent, not raised
            return Response(message=f"Could not attach {path}: {error}", break_loop=False)

        # Not `self.agent.context`. When the model dispatches this through the
        # `parallel` tool it runs in a worker context that is deleted as soon as
        # the job returns, taking the download control and the file's owning
        # conversation with it.
        surface = user_facing_context(self.agent)

        record = store.save(
            owner,
            filename=name or os.path.basename(path),
            content=content,
            context_id=surface.id,
            source_path=path,
            max_files=settings["max_files_per_user"],
        )

        # The log item is what the interface turns into a download control. The
        # kvps already travel to the client, so this needs no protocol change.
        surface.log.log(
            type="info",
            heading=f"Attached {record['filename']}",
            content=f"{record['filename']} ({_readable(record['bytes'])}) is ready to download.",
            kvps={
                "attachment_id": record["id"],
                "attachment_name": record["filename"],
                "attachment_bytes": str(record["bytes"]),
            },
        )

        return Response(
            message=(
                f"Attached {record['filename']} ({_readable(record['bytes'])}). "
                "The user can download it from this message."
            ),
            break_loop=False,
        )

    async def _read(self, path: str, owner: str, max_bytes: int) -> bytes:
        endpoint = await runner.resolve_endpoint(owner)
        if endpoint is not None:
            return await runner.fetch(endpoint, path, max_bytes)

        # No runner configured: agent code ran in this container, so the file is
        # on this filesystem. Development only -- a deployment sets
        # REZZTOR_REQUIRE_REMOTE_EXECUTION and never reaches this branch.
        local = Path(workdir.confine_path(path, user_id=owner))
        if not local.is_file():
            raise runner.FetchError(f"No readable file at {path}.")
        size = local.stat().st_size
        if size > max_bytes:
            raise runner.FetchError(
                f"{path} is {size / 1_048_576:.1f} MB, over the "
                f"{max_bytes / 1_048_576:.0f} MB limit."
            )
        return local.read_bytes()


def _readable(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1_048_576:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1_048_576:.1f} MB"
