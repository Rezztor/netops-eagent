"""Size limits for unpacking an archive an account supplied.

One module so the file browser, the skills import and the backup restore refuse
the same things. The caller still refuses unsafe names and links; this only
bounds how much an archive may expand into: member count, the largest member,
the total, and the compression ratio.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

GIB = 1 << 30
MIB = 1 << 20


class ArchiveTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    total: int = GIB
    members: int = 20_000
    member: int = 512 * MIB
    ratio: int = 200  # uncompressed : compressed, judged only above RATIO_FLOOR


# Backups hold a whole instance: more files and bigger files, and text that
# compresses far better than 200:1.
BACKUP_LIMITS = Limits(total=20 * GIB, members=1_000_000, member=5 * GIB, ratio=1000)
RATIO_FLOOR = MIB  # a few tiny members compress absurdly well and mean nothing


def _refuse(what: str) -> None:
    raise ArchiveTooLarge(f"Archive refused: {what}")


def _ratio_ok(size: int, packed: int, limits: Limits) -> bool:
    return size <= RATIO_FLOOR or size <= limits.ratio * max(packed, 1)


def check_zip(infos: list, limits: Limits = Limits()) -> None:
    """Refuse on what the central directory claims, before anything is written."""
    if len(infos) > limits.members:
        _refuse(f"more than {limits.members} members")
    if sum(i.file_size for i in infos) > limits.total:
        _refuse(f"unpacks to more than {limits.total // MIB} MiB")
    for i in infos:
        if i.file_size > limits.member:
            _refuse(f"a member is larger than {limits.member // MIB} MiB")
        if not _ratio_ok(i.file_size, i.compress_size, limits):
            _refuse(f"compression ratio above {limits.ratio}:1")


class Budget:
    """Counts what is really written, so a header that lies still hits the cap."""

    def __init__(self, limits: Limits = Limits()):
        self.limits, self.written = limits, 0

    def copy(self, src: BinaryIO, dst: BinaryIO, packed: int = 0) -> None:
        size = 0
        while chunk := src.read(MIB):
            size += len(chunk)
            self.written += len(chunk)
            if size > self.limits.member:
                _refuse(f"a member is larger than {self.limits.member // MIB} MiB")
            if self.written > self.limits.total:
                _refuse(f"unpacks to more than {self.limits.total // MIB} MiB")
            if not _ratio_ok(size, packed, self.limits):
                _refuse(f"compression ratio above {self.limits.ratio}:1")
            dst.write(chunk)


def extract_zip(archive, target: Path, limits: Limits = Limits()) -> None:
    """Unpack a ZipFile into target. Names must already have been vetted by the caller."""
    infos = archive.infolist()
    check_zip(infos, limits)
    budget = Budget(limits)
    for info in infos:
        dest = Path(target) / info.filename
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(dest, "wb") as out:
            budget.copy(src, out, info.compress_size)


def check_sizes(sizes: list[int], packed: int, limits: Limits = Limits()) -> None:
    """Refuse a tar or 7z by its member sizes; `packed` is the archive file's own size."""
    if len(sizes) > limits.members:
        _refuse(f"more than {limits.members} members")
    if max(sizes, default=0) > limits.member:
        _refuse(f"a member is larger than {limits.member // MIB} MiB")
    total = sum(sizes)
    if total > limits.total:
        _refuse(f"unpacks to more than {limits.total // MIB} MiB")
    if not _ratio_ok(total, packed, limits):
        _refuse(f"compression ratio above {limits.ratio}:1")


def check_tar(archive, packed: int, limits: Limits = Limits()) -> None:
    """Walk the tar headers of a freshly opened tar and stop at the first that breaks a limit.

    Nothing is unpacked, and a bomb is refused at its header. Open the tar again to extract.
    """
    sizes: list[int] = []
    total = 0
    for member in archive:
        sizes.append(member.size)
        total += member.size
        if len(sizes) > limits.members or member.size > limits.member or total > limits.total:
            break
    check_sizes(sizes, packed, limits)


def tree_size(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file() and not p.is_symlink())

