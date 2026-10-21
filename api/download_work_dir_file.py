import base64
from io import BytesIO
import mimetypes
import os
from pathlib import Path

from flask import Response
from helpers.api import ApiHandler, Input, Output, Request
from helpers import files, runtime
from api import file_info
from urllib.parse import quote



def stream_file_download(file_source, download_name, chunk_size=8192, delete_after=False):
    """
    Create a streaming response for file downloads that shows progress in browser.

    Args:
        file_source: Either a file path (str) or BytesIO object
        download_name: Name for the downloaded file
        chunk_size: Size of chunks to stream (default 8192 bytes)

    Returns:
        Flask Response object with streaming content
    """
    # Calculate file size for Content-Length header
    if isinstance(file_source, str):
        # File path - get size from filesystem
        file_size = os.path.getsize(file_source)
    elif isinstance(file_source, BytesIO):
        # BytesIO object - get size from buffer
        current_pos = file_source.tell()
        file_source.seek(0, 2)  # Seek to end
        file_size = file_source.tell()
        file_source.seek(current_pos)  # Restore original position
    else:
        raise ValueError(f"Unsupported file source type: {type(file_source)}")

    def generate():
        if isinstance(file_source, str):
            # File path - open and stream from disk
            with open(file_source, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        elif isinstance(file_source, BytesIO):
            # BytesIO object - stream from memory
            file_source.seek(0)  # Ensure we're at the beginning
            while True:
                chunk = file_source.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    # Detect content type based on file extension
    content_type, _ = mimetypes.guess_type(download_name)
    if not content_type:
        content_type = 'application/octet-stream'

    # Create streaming response with proper headers for immediate streaming
    response = Response(
        generate(),
        content_type=content_type,
        direct_passthrough=True,  # Prevent Flask from buffering the response
        headers={
            'Content-Disposition': make_disposition(download_name),
            'Content-Length': str(file_size),  # Critical for browser progress bars
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Accept-Ranges': 'bytes'  # Allow browser to resume downloads
        }
    )

    if delete_after and isinstance(file_source, str):
        # The archive of a folder is built in the temporary directory for this
        # one response. Nothing else ever removed it, so every folder download
        # left its whole zip behind until the container was replaced.
        response.call_on_close(lambda: os.path.exists(file_source) and os.unlink(file_source))

    return response


def make_disposition(download_name: str) -> str:
    # Basic ASCII fallback (strip or replace weird chars)
    ascii_fallback = download_name.encode("ascii", "ignore").decode("ascii") or "download"
    utf8_name = quote(download_name)  # URL-encode UTF-8 bytes

    # RFC 5987: filename* with UTF-8
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_name}'


def resolve_download_path(path: str) -> str:
    """Resolve a requested download path from the File Browser root.

    The root was `Path("/")`, which made the `relative_to` below succeed for
    every absolute path on the box -- a guard in shape only. It now resolves
    against the caller's own directory, the same root `FileBrowser` uses, so a
    download cannot reach what a listing cannot show.
    """
    from helpers.file_browser import FileBrowser

    browser = FileBrowser()
    candidate = Path(path)
    if not candidate.is_absolute():
        return str(browser.resolve_within(path))

    # A leading slash means one of two things and both are legitimate. The
    # handler prepends "/" to every relative path, and `get_files` reports each
    # entry as a path relative to the browser root -- so "/notes/x.txt" is
    # normally root-relative, not filesystem-absolute. It was unambiguous only
    # while the root was "/" and the two readings coincided.
    resolved = candidate.resolve()
    if resolved == browser.base_dir or resolved.is_relative_to(browser.base_dir):
        return str(resolved)

    # Only fall back to the root-relative reading when it names something that
    # is actually there. Without the existence check, any absolute path outside
    # the root would be rewritten into a path inside it and returned -- never a
    # leak, since the result is still contained, but it turns a clear refusal
    # into a 404 and hides a caller that is asking for the wrong thing.
    alternative = browser.base_dir.joinpath(path.lstrip("/")).resolve()
    if alternative.is_relative_to(browser.base_dir) and alternative.exists():
        return str(alternative)
    raise ValueError("Invalid file path")


class DownloadFile(ApiHandler):

    @classmethod
    def get_methods(cls):
        return ["GET"]

    async def process(self, input: Input, request: Request) -> Output:
        file_path = request.args.get("path", input.get("path", ""))
        if not file_path:
            raise ValueError("No file path provided")
        if not file_path.startswith("/"):
            file_path = f"/{file_path}"

        try:
            file_path = await runtime.call_development_function(
                resolve_download_path, file_path
            )
        except ValueError as exc:
            return Response(str(exc), status=400)

        file = await runtime.call_development_function(
            file_info.get_file_info, file_path
        )

        if not file["exists"]:
            raise Exception(f"File {file_path} not found")

        if file["is_dir"]:
            zip_file = await runtime.call_development_function(files.zip_dir, file["abs_path"])
            directory_name = os.path.basename(file_path.rstrip("/")) or "directory"
            download_name = f"{directory_name}.zip"
            if runtime.is_development():
                b64 = await runtime.call_development_function(fetch_file, zip_file)
                file_data = BytesIO(base64.b64decode(b64))
                return stream_file_download(
                    file_data,
                    download_name=download_name
                )
            else:
                return stream_file_download(
                    zip_file,
                    download_name=download_name,
                    delete_after=True,
                )
        elif file["is_file"]:
            if runtime.is_development():
                b64 = await runtime.call_development_function(fetch_file, file["abs_path"])
                file_data = BytesIO(base64.b64decode(b64))
                return stream_file_download(
                    file_data,
                    download_name=os.path.basename(file_path)
                )
            else:
                return stream_file_download(
                    file["abs_path"],
                    download_name=os.path.basename(file["file_name"])
                )
        raise Exception(f"File {file_path} not found")


async def fetch_file(path):
    with open(path, "rb") as file:
        file_content = file.read()
        return base64.b64encode(file_content).decode("utf-8")
