from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

from helpers import archive_limits, extension, file_browser, runtime
from helpers.api import ApiHandler, Input, Output, Request
from api import get_work_dir_files


ARCHIVE_SUFFIXES = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar", ".tgz", ".tbz", ".tbz2", ".txz",
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".zst",
)
TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".tgz", ".tbz", ".tbz2", ".txz")


class ExtractWorkDirArchive(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        path = str(input.get("path") or "").strip()
        if not path:
            return {"error": "Archive path is required"}
        if not path.startswith("/"):
            path = f"/{path}"

        try:
            extracted_path = await runtime.call_development_function(extract_archive, path)
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}

        current_path = str(input.get("currentPath") or "")
        await extension.call_extensions_async(
            "workdir_file_mutation_after",
            agent=None,
            data={
                "action": "extract",
                "path": extracted_path,
                "paths": [path, extracted_path],
                "current_path": current_path,
            },
        )
        listing = await runtime.call_development_function(get_work_dir_files.get_files, current_path)
        return {"data": listing, "extracted_path": extracted_path}


def extract_archive(path: str) -> str:
    source = resolve_archive_path(path)
    target = create_target_directory(source)
    try:
        kind = archive_kind(source)
        if kind == "zip":
            extract_zip(source, target)
        elif kind == "tar":
            extract_tar(source, target)
        else:
            extract_with_7zip(source, target)
        # After, not before: only now is the unpacked size known for every
        # format, and the except below removes what was written.
        file_browser.check_account_quota()
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return str(target)


def resolve_archive_path(path: str) -> Path:
    # The caller's own File Browser root, through the resolver the download
    # endpoints use. This checked against the application's base directory
    # instead, which is every account's files: any signed-in account could
    # unpack an archive into another one's folder, and the root-relative path
    # the interface sends ("/notes.zip") was the one path it refused.
    from api.download_work_dir_file import resolve_download_path

    resolved = Path(resolve_download_path(path))
    if not resolved.is_file():
        raise ValueError("Archive file was not found")
    return resolved


def archive_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(TAR_SUFFIXES):
        return "tar"
    if name.endswith(ARCHIVE_SUFFIXES):
        return "7zip"
    raise ValueError("Unsupported archive format")


def create_target_directory(source: Path) -> Path:
    name = source.name
    for suffix in ARCHIVE_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
            break
    name = name or "extracted"
    target = source.parent / name
    index = 2
    while target.exists():
        target = source.parent / f"{name}-{index}"
        index += 1
    target.mkdir()
    return target


def safe_member_path(target: Path, name: str) -> Path:
    if not name or name.startswith(("/", "\\")) or "\\" in name or ".." in Path(name).parts:
        raise ValueError("Archive contains an unsafe path")
    destination = (target / name).resolve(strict=False)
    try:
        destination.relative_to(target.resolve())
    except ValueError as exc:
        raise ValueError("Archive contains an unsafe path") from exc
    return destination


def extract_zip(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            safe_member_path(target, member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Archive contains a symbolic link")
        archive_limits.extract_zip(archive, target)


def extract_tar(source: Path, target: Path) -> None:
    with tarfile.open(source, "r:*") as probe:
        archive_limits.check_tar(probe, source.stat().st_size)
    with tarfile.open(source, "r:*") as archive:
        for member in archive.getmembers():
            safe_member_path(target, member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("Archive contains a symbolic link or device")
        archive.extractall(target, filter="data")


# 7-Zip names a link member with one of these properties, or with an
# attribute string carrying the Windows reparse-point letter "L" or a Unix
# mode of type "l".
LINK_PROPERTIES = ("Symbolic Link = ", "Hard Link = ", "Link = ", "Copy Link = ")
UNIX_LINK_MODE = re.compile(r"(?:^|\s)l[-rwxsStT]{9}(?:\s|$)")


def is_link_listing_line(line: str) -> bool:
    if line.startswith(LINK_PROPERTIES):
        return bool(line.split(" = ", 1)[1].strip())
    if line.startswith("Attributes = "):
        value = line.removeprefix("Attributes = ").strip()
        return bool(value) and ("L" in value.split()[0] or bool(UNIX_LINK_MODE.search(value)))
    return False


def extract_with_7zip(source: Path, target: Path) -> None:
    binary = shutil.which("7z") or shutil.which("7zz")
    if not binary:
        raise ValueError("This archive format requires 7-Zip in the runtime image")
    listing = subprocess.run(
        [binary, "l", "-slt", str(source)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    marker = "----------"
    if marker not in listing:
        raise ValueError("Could not inspect archive safely")
    sizes = []
    for line in listing.split(marker, 1)[1].splitlines():
        if line.startswith("Path = "):
            safe_member_path(target, line.removeprefix("Path = "))
        elif line.startswith("Size = ") and line[7:].isdigit():
            sizes.append(int(line[7:]))
        elif is_link_listing_line(line):
            # Refused before 7-Zip writes anything: a link it created could
            # carry a later member's write out of the target.
            raise ValueError("Archive contains a symbolic link")
    archive_limits.check_sizes(sizes, source.stat().st_size)
    # A private staging folder, moved into place only once it has been
    # checked, so nothing half-extracted is ever visible under the target name.
    staging = Path(tempfile.mkdtemp(prefix=".extract-", dir=target.parent))
    try:
        subprocess.run([binary, "x", "-y", f"-o{staging}", str(source)], check=True, capture_output=True)
        if any(path.is_symlink() for path in staging.rglob("*")):
            raise ValueError("Archive contains a symbolic link")
        # ponytail: 7-Zip cannot be metered while it writes; the listing is checked first and the result after.
        if archive_limits.tree_size(staging) > archive_limits.Limits().total:
            raise archive_limits.ArchiveTooLarge("Archive refused: unpacked size is over the limit")
        os.replace(staging, target)  # target is the empty folder made for it
    finally:
        shutil.rmtree(staging, ignore_errors=True)
