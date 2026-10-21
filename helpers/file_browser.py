import os
from pathlib import Path
import shutil
import base64
import subprocess
import threading
from typing import Dict, List, Tuple, Any
from helpers.security import safe_filename
from datetime import datetime

from helpers import files
from helpers.localization import Localization
from helpers.print_style import PrintStyle

# Bytes one account may hold across its data root (usr/users/<id>) and its
# working directory, checked before an upload or after an extraction. Each
# request is already bounded; this bounds the account, so one of them cannot
# fill the volume every account shares.
ACCOUNT_QUOTA_BYTES = int(os.environ.get("REZZTOR_ACCOUNT_QUOTA_BYTES") or 50 * (1 << 30))


class QuotaExceeded(ValueError):
    pass


_quota_locks: dict[str, threading.RLock] = {}
_quota_locks_guard = threading.Lock()


def quota_lock() -> threading.RLock:
    """The current account's lock, held across a quota check and the writes it allowed.

    Without it, N uploads that each pass the check before any is written can
    land N times the room that was left. Hold it only around synchronous code:
    nothing awaits inside it.
    """
    # ponytail: one process holds every request (uvicorn runs one worker);
    # a second worker process would need the lock in PostgreSQL instead.
    from helpers import user_scope

    key = str(user_scope.get_current_user_id() or "")
    with _quota_locks_guard:
        return _quota_locks.setdefault(key, threading.RLock())


def _tree_bytes(root: str | Path) -> int:
    total = 0
    stack = [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def account_usage(workdir_root: str | Path | None = None) -> int:
    """Bytes the current account holds: its working directory plus its data root."""
    from helpers import user_scope

    # ponytail: a full walk on every upload, O(files); keep a running total if accounts grow huge.
    roots = {str(workdir_root or FileBrowser().base_dir)}
    user_id = user_scope.get_current_user_id()
    if user_scope.is_safe_user_id(user_id):
        roots.add(user_scope.get_user_root(user_id))
    return sum(_tree_bytes(root) for root in roots)


def check_account_quota(incoming: int = 0, workdir_root: str | Path | None = None) -> None:
    if account_usage(workdir_root) + incoming > ACCOUNT_QUOTA_BYTES:
        raise QuotaExceeded("Storage quota reached: delete some files first")


def stream_size(stream) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


class FileBrowser:
    ALLOWED_EXTENSIONS = {
        'image': {'jpg', 'jpeg', 'png', 'bmp'},
        'code': {'py', 'js', 'sh', 'html', 'css'},
        'document': {'md', 'pdf', 'txt', 'csv', 'json'}
    }

    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    MAX_TEXT_FILE_SIZE = 1 * 1024 * 1024  # 1MB

    def __init__(self, base_dir: str | Path | None = None):
        """Root this browser at one directory, by default the caller's own.

        This used to be the filesystem root. Every containment check below is
        `is_relative_to(self.base_dir)`, and against `/` all of them are true --
        so the guards read as guards and enforced nothing. A signed-in account
        of any role could list `/`, read `usr/.env` and `/proc/self/environ`,
        and reach every other tenant's files.

        The default is now the per-user working directory that
        `helpers.workdir` already computes for agent-executed code, so the file
        browser and the agent see the same tree and ADR 0003's "ownership is
        derived from where a file lives" holds for both.

        `base_dir` is for callers that own their tree already (tests, and any
        future caller with a different root). It is not reachable from a
        request: no API handler passes it.
        """
        from helpers import workdir

        # scope_to_user, not get_workdir_path: the latter normalises to the
        # container's /eagent/... form, which is the right answer to give the
        # agent and the wrong one to open a file with outside the container.
        resolved = (
            Path(base_dir)
            if base_dir
            else Path(workdir.scope_to_user(workdir.get_workdir_base()))
        )
        # The browser is the only way some users touch their directory, so it
        # creates it rather than reporting "not found" on a fresh account.
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError:
            # A read-only or otherwise unusable root still yields a browser that
            # refuses every path, which is the safe direction.
            pass
        self.base_dir = resolved.resolve()

    def resolve_within(self, *parts: str) -> Path:
        """Resolve a request-supplied path and prove it stayed inside base_dir.

        `is_relative_to` rather than `str.startswith`: the string form accepts
        `/home/user-evil` for a base of `/home/user`, because a prefix is not a
        path boundary. `resolve()` first, so `..` and symlinks are collapsed
        before the comparison rather than after it.
        """
        given = [str(part) for part in parts if part]
        candidate = (
            self._rooted(given[0]).joinpath(*given[1:]) if given else self.base_dir
        ).resolve()
        if candidate != self.base_dir and not candidate.is_relative_to(self.base_dir):
            raise ValueError("Invalid path")
        return candidate

    def _rooted(self, path: str) -> Path:
        """A request path placed under base_dir, before any containment check.

        The listing reports paths relative to the root and the handlers put a
        slash in front of them, so "/notes/a.txt" means the root's `notes`, not
        the filesystem's. That was unambiguous while the root was "/"; once it
        became the caller's directory, joining an absolute path replaced the
        root and every delete, edit, rename and move the interface sent was
        refused as "Invalid path". A path already inside the root is kept as it
        is, and the root-relative reading is taken only when it names something
        that is there -- the rule the download resolvers follow -- so a path
        from outside the root is still refused rather than quietly rewritten.
        This only decides the reading; the caller still resolves and checks
        containment.
        """
        candidate = Path(str(path))
        if candidate.is_absolute() and not candidate.is_relative_to(self.base_dir):
            relative = self.base_dir / str(path).lstrip("/")
            if os.path.lexists(relative):
                return relative
        return self.base_dir / candidate

    def save_file_b64(self, current_path: str, filename: str, base64_content: str):
        try:
            # Resolve the target directory path
            target_file = self.resolve_within(current_path, filename)
            content = base64.b64decode(base64_content)
            if len(content) > self.MAX_FILE_SIZE:
                raise ValueError("File is over the upload size limit")
            with quota_lock():
                check_account_quota(len(content), self.base_dir)
                os.makedirs(target_file.parent, exist_ok=True)
                with open(target_file, "wb") as file:
                    file.write(content)
            return True
        except Exception as e:
            PrintStyle.error(f"Error saving file {filename}: {e}")
            return False

    def save_files(self, files: List, current_path: str = "") -> Tuple[List[str], List[str]]:
        """Save uploaded files and return successful and failed filenames"""
        successful = []
        failed = []

        try:
            # Resolve the target directory path
            target_dir = self.resolve_within(current_path)

            os.makedirs(target_dir, exist_ok=True)
            with quota_lock():
                used = account_usage(self.base_dir)

                for file in files:
                    try:
                        if file and self._is_allowed_file(file.filename, file):
                            filename = safe_filename(file.filename)
                            if not filename:
                                raise ValueError("Invalid filename")
                            size = stream_size(file.stream)
                            if size > self.MAX_FILE_SIZE:
                                raise ValueError("File is over the upload size limit")
                            if used + size > ACCOUNT_QUOTA_BYTES:
                                raise QuotaExceeded("Storage quota reached")
                            file_path = target_dir / filename

                            file.save(str(file_path))
                            used += size
                            successful.append(filename)
                        else:
                            failed.append(file.filename)
                    except Exception as e:
                        PrintStyle.error(f"Error saving file {file.filename}: {e}")
                        failed.append(file.filename)

            return successful, failed

        except Exception as e:
            PrintStyle.error(f"Error in save_files: {e}")
            return successful, failed

    def delete_file(self, file_path: str) -> bool:
        """Delete a file or empty directory"""
        try:
            # Resolve the full path while preventing directory traversal
            full_path = self.resolve_within(file_path)
            if full_path == self.base_dir:
                # An empty or "/" path is the root itself, and removing that is
                # removing everything the account has.
                raise ValueError("The root folder cannot be deleted")

            if os.path.exists(full_path):
                if os.path.isfile(full_path):
                    os.remove(full_path)
                elif os.path.isdir(full_path):
                    shutil.rmtree(full_path)
                return True

            return False

        except Exception as e:
            PrintStyle.error(f"Error deleting {file_path}: {e}")
            return False

    def rename_item(self, file_path: str, new_name: str) -> bool:
        try:
            if not new_name or new_name in {".", ".."}:
                raise ValueError("Invalid new name")
            if "/" in new_name or "\\" in new_name:
                raise ValueError("New name cannot include path separators")

            full_path = self.resolve_within(file_path)
            if full_path == self.base_dir:
                raise ValueError("The root folder cannot be renamed")
            if not full_path.exists():
                raise FileNotFoundError("File or folder not found")

            new_path = self.resolve_within(full_path.with_name(new_name))
            if full_path == new_path:
                return True
            if new_path.exists():
                raise FileExistsError("Target already exists")

            os.rename(full_path, new_path)
            return True
        except Exception as e:
            PrintStyle.error(f"Error renaming {file_path}: {e}")
            raise

    def move_items(self, file_paths: List[str], destination_path: str) -> List[str]:
        if not file_paths:
            raise ValueError("No items selected")

        base_dir = self.base_dir.resolve()
        destination = self._rooted(destination_path).resolve()
        if not destination.is_relative_to(base_dir):
            raise ValueError("Invalid destination path")
        if not destination.is_dir():
            raise NotADirectoryError("Destination folder not found")

        moves: List[Tuple[Path, Path]] = []
        targets: set[Path] = set()
        for file_path in dict.fromkeys(file_paths):
            requested = self._rooted(file_path)
            source = requested.parent.resolve() / requested.name
            if not source.is_relative_to(base_dir) or source == base_dir:
                raise ValueError("Invalid source path")
            if not source.exists() and not source.is_symlink():
                raise FileNotFoundError(f"Item not found: {source.name}")
            if source == destination:
                raise ValueError("A folder cannot be moved into itself")
            if (
                source.is_dir()
                and not source.is_symlink()
                and destination.is_relative_to(source)
            ):
                raise ValueError("A folder cannot be moved into itself")

            target = destination / source.name
            if target == source:
                raise ValueError(f"{source.name} is already in this folder")
            if target.exists() or target.is_symlink():
                raise FileExistsError(
                    f'An item named "{source.name}" already exists'
                )
            if target in targets:
                raise FileExistsError(f'Multiple items are named "{source.name}"')
            targets.add(target)
            moves.append((source, target))

        moved: List[Tuple[Path, Path]] = []
        try:
            for source, target in moves:
                os.rename(source, target)
                moved.append((source, target))
        except Exception:
            for source, target in reversed(moved):
                try:
                    os.rename(target, source)
                except Exception as rollback_error:
                    PrintStyle.error(f"Error restoring {source}: {rollback_error}")
            raise

        return [str(target) for _, target in moved]

    def create_folder(self, parent_path: str, folder_name: str) -> bool:
        try:
            if not folder_name or folder_name in {".", ".."}:
                raise ValueError("Invalid folder name")
            if "/" in folder_name or "\\" in folder_name:
                raise ValueError("Folder name cannot include path separators")

            parent_full = self.resolve_within(parent_path)
            target_dir = self.resolve_within(parent_full / folder_name)
            if target_dir.exists():
                raise FileExistsError("Folder already exists")

            os.makedirs(target_dir, exist_ok=False)
            return True
        except Exception as e:
            PrintStyle.error(f"Error creating folder {folder_name}: {e}")
            raise

    def save_text_file(self, file_path: str, content: str) -> bool:
        try:
            if not isinstance(content, str):
                raise ValueError("Content must be a string")
            content_size = len(content.encode("utf-8"))
            if content_size > self.MAX_TEXT_FILE_SIZE:
                raise ValueError("File exceeds 1 MB and cannot be edited")

            full_path = self.resolve_within(file_path)
            if full_path.exists() and full_path.is_dir():
                raise ValueError("Target is a directory")

            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as file:
                file.write(content)
            return True
        except Exception as e:
            PrintStyle.error(f"Error saving file {file_path}: {e}")
            raise

    def _is_allowed_file(self, filename: str, file) -> bool:
        # allow any file to be uploaded in file browser

        # if not filename:
        #     return False
        # ext = self._get_file_extension(filename)
        # all_allowed = set().union(*self.ALLOWED_EXTENSIONS.values())
        # if ext not in all_allowed:
        #     return False

        return True  # Allow the file if it passes the checks

    def _get_file_extension(self, filename: str) -> str:
        return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

    def _get_files_via_ls(self, full_path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Get files and folders using ls command for better error handling"""
        files: List[Dict[str, Any]] = []
        folders: List[Dict[str, Any]] = []

        try:
            # Use ls command to get directory listing
            result = subprocess.run(
                ['ls', '-la', str(full_path)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                PrintStyle.error(f"ls command failed: {result.stderr}")
                return files, folders

            # Parse ls output (skip first line which is "total X")
            lines = result.stdout.strip().split('\n')
            if len(lines) <= 1:
                return files, folders

            for line in lines[1:]:  # Skip the "total" line
                try:
                    # Skip current and parent directory entries
                    if line.endswith(' .') or line.endswith(' ..'):
                        continue

                    # Parse ls -la output format
                    parts = line.split()
                    if len(parts) < 9:
                        continue

                    # Check if this is a symlink (permissions start with 'l')
                    permissions = parts[0]
                    is_symlink = permissions.startswith('l')

                    if is_symlink:
                        # For symlinks, extract the name before the '->' arrow
                        full_name_part = ' '.join(parts[8:])
                        if ' -> ' in full_name_part:
                            filename = full_name_part.split(' -> ')[0]
                            symlink_target = full_name_part.split(' -> ')[1]
                        else:
                            filename = full_name_part
                            symlink_target = None
                    else:
                        filename = ' '.join(parts[8:])  # Handle filenames with spaces
                        symlink_target = None

                    if not filename:
                        continue

                    # Get full path for this entry
                    entry_path = full_path / filename

                    try:
                        stat_info = entry_path.stat()

                        entry_data: Dict[str, Any] = {
                            "name": filename,
                            "path": str(entry_path.relative_to(self.base_dir)),
                            "modified": datetime.fromtimestamp(
                                stat_info.st_mtime,
                                tz=Localization.get().get_tzinfo(),
                            ).isoformat()
                        }

                        # Add symlink information if this is a symlink
                        if is_symlink and symlink_target:
                            entry_data["symlink_target"] = symlink_target
                            entry_data["is_symlink"] = True

                        if entry_path.is_file():
                            entry_data.update({
                                "type": self._get_file_type(filename),
                                "size": stat_info.st_size,
                                "is_dir": False
                            })
                            files.append(entry_data)
                        elif entry_path.is_dir():
                            entry_data.update({
                                "type": "folder",
                                "size": 0,  # Directories show as 0 bytes
                                "is_dir": True
                            })
                            folders.append(entry_data)

                    except (OSError, PermissionError, FileNotFoundError) as e:
                        # Log error but continue with other files
                        PrintStyle.warning(f"No access to {filename}: {e}")
                        continue

                    if len(files) + len(folders) > 10000:
                        break

                except Exception as e:
                    # Log error and continue with next line
                    PrintStyle.error(f"Error parsing ls line '{line}': {e}")
                    continue

        except subprocess.TimeoutExpired:
            PrintStyle.error("ls command timed out")
        except Exception as e:
            PrintStyle.error(f"Error running ls command: {e}")

        return files, folders

    def get_files(self, current_path: str = "") -> Dict:
        try:
            # Resolve the full path while preventing directory traversal
            full_path = self.resolve_within(current_path)
            if not full_path.exists():
                raise FileNotFoundError("Directory not found")
            if not full_path.is_dir():
                raise NotADirectoryError("Path is not a directory")

            # Use ls command instead of os.scandir for better error handling
            files, folders = self._get_files_via_ls(full_path)

            # Combine folders and files, folders first
            all_entries = folders + files

            # Get parent directory path if not at root
            parent_path = ""
            if current_path:
                try:
                    # Get the absolute path of current directory
                    current_abs = (self.base_dir / current_path).resolve()

                    # parent_path is empty only if we're already at root
                    if str(current_abs) != str(self.base_dir):
                        parent_path = str(Path(current_path).parent)

                except Exception:
                    parent_path = ""

            return {
                "entries": all_entries,
                "current_path": current_path,
                "parent_path": parent_path
            }

        except Exception as e:
            PrintStyle.error(f"Error reading directory: {e}")
            return {
                "entries": [],
                "current_path": current_path,
                "parent_path": "",
                "error": str(e),
            }

    def get_full_path(self, file_path: str, allow_dir: bool = False) -> str:
        """Get full file path if it exists and is within base_dir.

        The docstring always said "within base_dir"; nothing checked it until
        the containment above became real, so this is the claim catching up
        with the code rather than a new rule.
        """
        full_path = str(self.resolve_within(file_path))
        if not files.exists(full_path):
            raise ValueError(f"File {file_path} not found")
        return full_path

    def _get_file_type(self, filename: str) -> str:
        ext = self._get_file_extension(filename)
        for file_type, extensions in self.ALLOWED_EXTENSIONS.items():
            if ext in extensions:
                return file_type
        return 'unknown'
