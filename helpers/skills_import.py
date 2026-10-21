from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Tuple

from helpers import archive_limits, files
from helpers.skills import discover_skill_md_files


ConflictPolicy = Literal["skip", "overwrite", "rename"]

# Project skills folder name (inside .eaproj)
PROJECT_SKILLS_DIR = "skills"


@dataclass(slots=True)
class ImportPlanItem:
    src_root: Path
    src_skill_dir: Path
    dest_skill_dir: Path


@dataclass(slots=True)
class ImportResult:
    imported: List[Path]
    skipped: List[Path]
    source_root: Path
    destination_root: Path
    namespace: str


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _derive_namespace(source: Path) -> str:
    # Use stem for zip, name for directory
    return (source.stem or source.name or "import").strip()


def _candidate_skill_roots(source_dir: Path) -> List[Path]:
    """
    Heuristics to find likely skill roots inside a repo/pack:
    - <source>/skills
    - <source>/plugins/*/skills (Claude Code style)
    - fallback: <source>
    """
    candidates: List[Path] = []

    direct = source_dir / "skills"
    if direct.is_dir() and discover_skill_md_files(direct):
        candidates.append(direct)

    plugins = source_dir / "plugins"
    if plugins.is_dir():
        for child in plugins.iterdir():
            if not child.is_dir():
                continue
            skills_dir = child / "skills"
            if skills_dir.is_dir() and discover_skill_md_files(skills_dir):
                candidates.append(skills_dir)

    # Deduplicate while preserving order
    unique: List[Path] = []
    seen = set()
    for c in candidates:
        key = str(c.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique or [source_dir]


def _safe_extract_zip(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            name = member.filename
            if not name or name.startswith(("/", "\\")) or "\\" in name:
                raise ValueError(f"Unsafe zip entry path: {name!r}")

            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Refusing to extract symlink from zip: {name!r}")

            destination = target / name
            if not _is_within(destination, target):
                raise ValueError(f"Unsafe zip entry path: {name!r}")

        archive_limits.extract_zip(archive, target)


def extract_skills_zip(
    zip_path: Path,
    *,
    tmp_subdir: str = "skill_imports",
    prefix: str = "import",
) -> tuple[Path, Path]:
    """
    Extract a zip into a temp folder inside Rezztor EAgent's tmp directory.
    Returns (source_root, cleanup_root).
    """
    base_tmp = Path(files.get_abs_path("tmp", tmp_subdir))
    base_tmp.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = base_tmp / f"{prefix}_{zip_path.stem}_{stamp}"
    target.mkdir(parents=True, exist_ok=True)

    try:
        _safe_extract_zip(zip_path, target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    # If zip contains a single top-level folder, treat that as the root
    children = [p for p in target.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0], target
    return target, target


def build_import_plan(
    source: Path,
    dest_root: Path,
    *,
    namespace: Optional[str] = None,
) -> Tuple[List[ImportPlanItem], Path]:
    """
    Build a copy plan for importing skills from a source folder.

    Returns: (plan_items, source_root_dir_used_for_scan)
    """
    source_dir = source
    roots = _candidate_skill_roots(source_dir)
    plan: List[ImportPlanItem] = []
    ns = _one_folder_name((namespace or _derive_namespace(source)).strip(), "namespace")
    dest_ns_root = dest_root / ns

    for root in roots:
        for skill_md in discover_skill_md_files(root):
            skill_dir = skill_md.parent
            # Skip if the skill dir is already inside destination (prevents recursive import)
            if _is_within(skill_dir, dest_root):
                continue
            try:
                rel = skill_dir.resolve().relative_to(root.resolve())
            except Exception:
                # If relative fails due to symlink oddities, just use leaf folder name
                rel = Path(skill_dir.name)
            dest_dir = dest_ns_root / rel
            plan.append(ImportPlanItem(src_root=root, src_skill_dir=skill_dir, dest_skill_dir=dest_dir))

    # Deduplicate by destination path (keep first occurrence)
    seen_dest = set()
    deduped: List[ImportPlanItem] = []
    for item in plan:
        key = str(item.dest_skill_dir.resolve())
        if key in seen_dest:
            continue
        seen_dest.add(key)
        deduped.append(item)

    return deduped, roots[0]


def _one_folder_name(name: str, what: str) -> str:
    """`name` when it is a single folder name. It is joined under a root, and
    an absolute name replaces the root while `..` climbs out of it."""
    if not name or name in (".", "..") or os.path.basename(name) != name or "\\" in name:
        raise ValueError(f"The {what} must be a single folder name.")
    return name


def _resolve_conflict(dest: Path, policy: ConflictPolicy, dry_run: bool = False) -> Tuple[Path, bool]:
    """
    Returns (final_dest_path, should_copy). Deletes nothing on a dry run.
    """
    if not dest.exists():
        return dest, True

    if policy == "skip":
        return dest, False

    if policy == "overwrite":
        if not dry_run:
            shutil.rmtree(dest)
        return dest, True

    # rename
    i = 2
    while True:
        candidate = dest.with_name(f"{dest.name}_{i}")
        if not candidate.exists():
            return candidate, True
        i += 1


def get_project_skills_folder(project_name: str) -> Path:
    """Get the skills folder path for a project."""
    from helpers.projects import get_project_meta
    return Path(get_project_meta(project_name, PROJECT_SKILLS_DIR))


def get_agent_profile_skills_folder(profile_name: str) -> Path:
    return Path(files.get_abs_path("usr", "agents", _one_folder_name(profile_name, "agent profile"), "skills"))


def get_project_agent_profile_skills_folder(project_name: str, profile_name: str) -> Path:
    from helpers.projects import get_project_meta
    return Path(get_project_meta(project_name, "agents", _one_folder_name(profile_name, "agent profile"), "skills"))


def resolve_skills_destination_root(
    project_name: Optional[str],
    agent_profile: Optional[str],
) -> Path:
    if project_name and agent_profile:
        return get_project_agent_profile_skills_folder(project_name, agent_profile)
    if project_name:
        return get_project_skills_folder(project_name)
    if agent_profile:
        return get_agent_profile_skills_folder(agent_profile)
    return Path(files.get_abs_path("usr", "skills"))


def import_skills(
    source_path: str,
    *,
    namespace: Optional[str] = None,
    conflict: ConflictPolicy = "skip",
    dry_run: bool = False,
    project_name: Optional[str] = None,
    agent_profile: Optional[str] = None,
) -> ImportResult:
    """
    Import external Skills into usr/skills/<namespace>/...

    - source_path can be a directory or a .zip file
    - Uses heuristics to detect the Skills root(s)
    - Copies each skill folder (parent of SKILL.md) as-is
    """
    src = Path(source_path).expanduser()
    if not src.is_absolute():
        src = (Path.cwd() / src).resolve()

    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    # Checked before anything is unpacked or made.
    ns = _one_folder_name((namespace or _derive_namespace(src)).strip() or "import", "namespace")
    dest_root = resolve_skills_destination_root(project_name, agent_profile)
    dest_root.mkdir(parents=True, exist_ok=True)

    unpacked: Optional[Path] = None
    source_dir: Path
    if src.is_file() and src.suffix.lower() == ".zip":
        source_dir, unpacked = extract_skills_zip(src)
    elif src.is_dir():
        source_dir = src
    else:
        raise ValueError("Source must be a directory or a .zip file")

    imported: List[Path] = []
    skipped: List[Path] = []
    try:
        plan, root_used = build_import_plan(source_dir, dest_root, namespace=ns)
        for item in plan:
            final_dest, should_copy = _resolve_conflict(item.dest_skill_dir, conflict, dry_run)
            if not should_copy:
                skipped.append(item.dest_skill_dir)
                continue
            if dry_run:
                imported.append(final_dest)
                continue
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(item.src_skill_dir, final_dest)
            imported.append(final_dest)
    finally:
        # The unpacked copy is only the source of the copy above.
        if unpacked is not None:
            shutil.rmtree(unpacked, ignore_errors=True)

    return ImportResult(
        imported=imported,
        skipped=skipped,
        source_root=root_used,
        destination_root=dest_root,
        namespace=ns,
    )
