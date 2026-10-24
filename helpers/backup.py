import zipfile
import json
import os
import tempfile
import datetime
import hashlib
import platform
import shutil
import stat
from typing import List, Dict, Any, Optional

from pathspec import PathSpec

from helpers import files, runtime, git, dotenv
from helpers import archive_limits, backup_crypto, db
from helpers.localization import Localization
from helpers.print_style import PrintStyle
from helpers.security import safe_filename


class BackupService:
    """
    Core backup and restore service for Rezztor EAgent.

    Features:
    - JSON-based metadata with user-editable path specifications
    - Comprehensive system information collection
    - Checksum validation for integrity
    - RFC compatibility through existing file helpers
    - Git version integration consistent with main application
    """

    def __init__(self):
        self.eagent_version = self._get_eagent_version()
        self.eagent_root = files.get_abs_path("")  # Resolved Rezztor EAgent root

        # Build base paths map for pattern resolution
        self.base_paths = {
            self.eagent_root: self.eagent_root,
        }

    def get_default_backup_metadata(self) -> Dict[str, Any]:
        """Get default backup patterns and metadata"""
        timestamp = Localization.get().now_iso()

        default_patterns = self._get_default_patterns()
        include_patterns, exclude_patterns = self._parse_patterns(default_patterns)

        return {
            "backup_name": f"rezztor-eagent-backup-{timestamp[:10]}",
            "include_hidden": True,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "backup_config": {
                "compression_level": 6,
                "integrity_check": True
            }
        }

    #: Where the database lives inside the archive. Beside metadata.json rather
    #: than under usr/, because it is not a file this instance has -- restoring
    #: it into the volume would write a stray file nothing reads.
    DATABASE_ENTRY = "database.json"

    def _get_default_patterns(self) -> str:
        """Get default backup patterns with resolved absolute paths.

        Only includes Rezztor EAgent project directory patterns.
        """
        # Ensure paths don't have double slashes
        agent_root = self.eagent_root.rstrip('/')

        return f"""# User data
# All persistent user data is now centralized in /usr for easier backup and restore
{agent_root}/usr/**
!{agent_root}/usr/.time_travel/**
"""

    def _get_eagent_version(self) -> str:
        """Get current Rezztor EAgent version"""
        try:
            # Get version from git info (same as run_ui.py)
            gitinfo = git.get_git_info()
            return gitinfo.get("version", "development")
        except Exception:
            return "unknown"

    def _resolve_path(self, pattern_path: str) -> str:
        """Resolve pattern path to absolute system path (now patterns are already absolute)"""
        return pattern_path

    def _unresolve_path(self, abs_path: str) -> str:
        """Convert absolute path back to pattern path (now patterns are already absolute)"""
        return abs_path

    def _parse_patterns(self, patterns: str) -> tuple[list[str], list[str]]:
        """Parse patterns string into include and exclude pattern arrays"""
        include_patterns = []
        exclude_patterns = []

        for line in patterns.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('!'):
                # Exclude pattern
                exclude_patterns.append(line[1:])  # Remove the '!' prefix
            else:
                # Include pattern
                include_patterns.append(line)

        return include_patterns, exclude_patterns

    def _patterns_to_string(self, include_patterns: list[str], exclude_patterns: list[str]) -> str:
        """Convert pattern arrays back to patterns string for pathspec processing"""
        patterns = []

        # Add include patterns
        for pattern in include_patterns:
            patterns.append(pattern)

        # Add exclude patterns with '!' prefix
        for pattern in exclude_patterns:
            patterns.append(f"!{pattern}")

        return '\n'.join(patterns)

    async def _get_system_info(self) -> Dict[str, Any]:
        """Collect system information for metadata"""
        import psutil

        try:
            return {
                "platform": platform.platform(),
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "architecture": platform.architecture()[0],
                "hostname": platform.node(),
                "python_version": platform.python_version(),
                "cpu_count": str(psutil.cpu_count()),
                "memory_total": str(psutil.virtual_memory().total),
                "disk_usage": str(psutil.disk_usage('/').total if os.path.exists('/') else 0)
            }
        except Exception as e:
            return {"error": f"Failed to collect system info: {str(e)}"}

    async def _get_environment_info(self) -> Dict[str, Any]:
        """Collect environment information for metadata"""
        try:
            return {
                "user": os.environ.get("USER", "unknown"),
                "home": os.environ.get("HOME", "unknown"),
                "shell": os.environ.get("SHELL", "unknown"),
                "path": os.environ.get("PATH", "")[:200] + "..." if len(os.environ.get("PATH", "")) > 200 else os.environ.get("PATH", ""),
                "timezone": Localization.get().get_timezone(),
                "working_directory": os.getcwd(),
                "eagent_root": files.get_abs_path(""),
                "runtime_mode": "development" if runtime.is_development() else "production"
            }
        except Exception as e:
            return {"error": f"Failed to collect environment info: {str(e)}"}

    async def _get_backup_author(self) -> str:
        """Get backup author/system identifier"""
        try:
            import getpass
            username = getpass.getuser()
            hostname = platform.node()
            return f"{username}@{hostname}"
        except Exception:
            return "unknown"

    def _count_directories(self, matched_files: List[Dict[str, Any]]) -> int:
        """Count unique directories in file list"""
        directories = set()
        for file_info in matched_files:
            dir_path = os.path.dirname(file_info["path"])
            if dir_path:
                directories.add(dir_path)
        return len(directories)

    def _get_explicit_patterns(self, include_patterns: List[str]) -> set[str]:
        """Extract explicit (non-wildcard) patterns that should always be included"""
        explicit_patterns = set()

        for pattern in include_patterns:
            # If pattern doesn't contain wildcards, it's explicit
            if '*' not in pattern and '?' not in pattern:
                # Remove leading slash for comparison
                explicit_patterns.add(pattern.lstrip('/'))

                # Also add parent directories as explicit (so hidden dirs can be traversed)
                path_parts = pattern.lstrip('/').split('/')
                for i in range(1, len(path_parts)):
                    parent_path = '/'.join(path_parts[:i])
                    explicit_patterns.add(parent_path)

        return explicit_patterns

    def _is_explicitly_included(self, file_path: str, explicit_patterns: set[str]) -> bool:
        """Check if a file/directory is explicitly included in patterns"""
        relative_path = file_path.lstrip('/')
        return relative_path in explicit_patterns

    def _translate_patterns(self, patterns: List[str], backup_metadata: Dict[str, Any]) -> List[str]:
        """Translate patterns from backed up system to current system.

        Replaces the backed up Rezztor EAgent root path with the current Rezztor EAgent root path
        in all patterns if there's an exact match at the beginning of the pattern.

        Args:
            patterns: List of patterns from the backed up system
            backup_metadata: Backup metadata containing the original eagent_root

        Returns:
            List of translated patterns for the current system
        """
        # Get the backed up rezztor eagent root path from metadata
        environment_info = backup_metadata.get("environment_info", {})
        backed_up_agent_root = environment_info.get("eagent_root", "")

        # Get current rezztor eagent root path
        current_agent_root = self.eagent_root

        # If we don't have the backed up root path, return patterns as-is
        if not backed_up_agent_root:
            return patterns

        # Ensure paths have consistent trailing slash handling
        backed_up_agent_root = backed_up_agent_root.rstrip('/')
        current_agent_root = current_agent_root.rstrip('/')

        translated_patterns = []
        for pattern in patterns:
            # Check if the pattern starts with the backed up rezztor eagent root
            if pattern.startswith(backed_up_agent_root + '/') or pattern == backed_up_agent_root:
                # Replace the backed up root with the current root
                relative_pattern = pattern[len(backed_up_agent_root):].lstrip('/')
                if relative_pattern:
                    translated_pattern = current_agent_root + '/' + relative_pattern
                else:
                    translated_pattern = current_agent_root
                translated_patterns.append(translated_pattern)
            else:
                # Pattern doesn't start with backed up agent root, keep as-is
                translated_patterns.append(pattern)

        return translated_patterns

    async def test_patterns(self, metadata: Dict[str, Any], max_files: Optional[int] = 1000) -> List[Dict[str, Any]]:
        """Test backup patterns and return list of matched files.

        Pass max_files=None for internal flows that must process the complete
        match set, such as backup creation and restore cleanup.
        """
        include_patterns = metadata.get("include_patterns", [])
        exclude_patterns = metadata.get("exclude_patterns", [])
        include_hidden = metadata.get("include_hidden", True)

        # Convert to patterns string for pathspec
        patterns_string = self._patterns_to_string(include_patterns, exclude_patterns)

        # Parse patterns using pathspec
        pattern_lines = [line.strip() for line in patterns_string.split('\n') if line.strip() and not line.strip().startswith('#')]

        if not pattern_lines:
            return []

        # Get explicit patterns for hidden file handling
        explicit_patterns = self._get_explicit_patterns(include_patterns)

        has_limit = max_files is not None
        matched_files = []
        processed_count = 0

        try:
            spec = PathSpec.from_lines("gitignore", pattern_lines)

            # Walk through base directories
            for base_pattern_path, base_real_path in self.base_paths.items():
                if not os.path.exists(base_real_path):
                    continue

                for root, dirs, files_list in os.walk(base_real_path):
                    # Filter hidden directories if not included, BUT allow explicit ones
                    if not include_hidden:
                        dirs_to_keep = []
                        for d in dirs:
                            if not d.startswith('.'):
                                dirs_to_keep.append(d)
                            else:
                                # Check if this hidden directory is explicitly included
                                dir_path = os.path.join(root, d)
                                pattern_path = self._unresolve_path(dir_path)
                                if self._is_explicitly_included(pattern_path, explicit_patterns):
                                    dirs_to_keep.append(d)
                        dirs[:] = dirs_to_keep

                    for file in files_list:
                        if has_limit and processed_count >= max_files:
                            break

                        file_path = os.path.join(root, file)
                        pattern_path = self._unresolve_path(file_path)

                        # Skip hidden files if not included, BUT allow explicit ones
                        if not include_hidden and file.startswith('.'):
                            if not self._is_explicitly_included(pattern_path, explicit_patterns):
                                continue

                        # Remove leading slash for pathspec matching
                        relative_path = pattern_path.lstrip('/')

                        if spec.match_file(relative_path):
                            try:
                                stat = os.stat(file_path)
                                matched_files.append({
                                    "path": pattern_path,
                                    "real_path": file_path,
                                    "size": stat.st_size,
                                    "modified": datetime.datetime.fromtimestamp(
                                        stat.st_mtime,
                                        tz=Localization.get().get_tzinfo(),
                                    ).isoformat(),
                                    "type": "file"
                                })
                                processed_count += 1
                            except (OSError, IOError):
                                # Skip files we can't access
                                continue

                    if has_limit and processed_count >= max_files:
                        break

                if has_limit and processed_count >= max_files:
                    break

        except Exception as e:
            raise Exception(f"Error processing patterns: {str(e)}")

        return matched_files

    @staticmethod
    def safe_backup_name(backup_name: object) -> str:
        """The requested name as one file name, or the default when nothing usable is left."""
        return safe_filename(str(backup_name or "")) or "rezztor-eagent-backup"

    async def create_backup(
        self,
        include_patterns: List[str],
        exclude_patterns: List[str],
        include_hidden: bool = True,
        backup_name: str = "rezztor-eagent-backup",
        *,
        passphrase: str,
    ) -> str:
        """Create an encrypted backup and return the path to the created file.

        The archive holds the instance's keys, password hashes and every user's
        data, so it is only ever written sealed with `passphrase`
        (`backup_crypto`). The plaintext zip exists only inside the temporary
        directory, mode 0600, until it has been sealed.
        """

        backup_crypto.check_passphrase(passphrase)

        # Create metadata for test_patterns
        metadata = {
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "include_hidden": include_hidden
        }

        # Get the complete matched file set. Preview and dry-run callers may
        # cap their scans for UI responsiveness, but the archive itself must be
        # complete.
        matched_files = await self.test_patterns(metadata, max_files=None)

        if not matched_files and not db.data_tables():
            raise Exception("No files matched the backup patterns")

        # Note what is *not* refused: no files, but a database with rows in it.
        # That used to be impossible, because everything was a file. It is now
        # the ordinary shape of a fresh instance -- accounts and settings live in
        # the database and usr/ can be empty -- and refusing would mean the one
        # instance most in need of a backup cannot take one.

        # Create temporary zip file
        temp_dir = tempfile.mkdtemp()
        # A file name, never a path: the caller deletes the directory the
        # archive is in, and `../x` or an absolute name chose that directory.
        backup_name = self.safe_backup_name(backup_name)
        zip_path = os.path.join(temp_dir, "plain.zip")
        sealed_path = os.path.join(temp_dir, f"{backup_name}{backup_crypto.EXTENSION}")

        try:
            plain = os.fdopen(os.open(zip_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
            with plain, zipfile.ZipFile(plain, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add comprehensive metadata
                metadata = {
                    # Basic backup information
                    "eagent_version": self.eagent_version,
                    "timestamp": Localization.get().now_iso(),
                    "backup_name": backup_name,
                    "include_hidden": include_hidden,

                    # Pattern arrays for granular control during restore
                    "include_patterns": include_patterns,
                    "exclude_patterns": exclude_patterns,

                    # System and environment information
                    "system_info": await self._get_system_info(),
                    "environment_info": await self._get_environment_info(),
                    "backup_author": await self._get_backup_author(),

                    # Backup configuration
                    "backup_config": {
                        "include_patterns": include_patterns,
                        "exclude_patterns": exclude_patterns,
                        "include_hidden": include_hidden,
                        "compression_level": 6,
                        "integrity_check": True
                    },

                    # File information
                    "files": [
                        {
                            "path": f["path"],
                            "size": f["size"],
                            "modified": f["modified"],
                            "type": "file"
                        }
                        for f in matched_files
                    ],

                    # Statistics
                    "total_files": len(matched_files),
                    "backup_size": sum(f["size"] for f in matched_files),
                    "directory_count": self._count_directories(matched_files),
                }

                zipf.writestr("metadata.json", json.dumps(metadata, indent=2))

                # The database, beside the files.
                #
                # `usr/**` stopped describing this instance's state the moment
                # accounts left it. A backup that still only zipped the volume
                # would restore an instance with conversations and no accounts,
                # which does not fail -- it seeds a fresh administrator and looks
                # like a working install with everyone's sign-in gone.
                zipf.writestr(self.DATABASE_ENTRY, json.dumps(db.dump_data()))

                # Add files
                for file_info in matched_files:
                    real_path = file_info["real_path"]
                    archive_path = file_info["path"].lstrip('/')

                    try:
                        if os.path.exists(real_path) and os.path.isfile(real_path):
                            zipf.write(real_path, archive_path)
                    except (OSError, IOError) as e:
                        # Log error but continue with other files
                        PrintStyle().warning(f"Warning: Could not backup file {real_path}: {e}")
                        continue

            backup_crypto.encrypt_file(zip_path, sealed_path, passphrase)
            os.remove(zip_path)
            return sealed_path

        except Exception as e:
            # Cleanup on error: the directory, not just the half-written zip
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise Exception(f"Error creating backup: {str(e)}")

    @staticmethod
    def _open_upload(backup_file, temp_dir: str, passphrase: Optional[str]) -> tuple[str, bool, str]:
        """Save an uploaded backup into `temp_dir`; (zip path, was encrypted, sha256 of the upload).

        An encrypted backup is decrypted beside it (0600) and the encrypted copy
        removed; a wrong passphrase or a damaged file raises BackupCryptoError
        with nothing left behind. An old plain zip passes through unchanged.
        """
        saved = os.path.join(temp_dir, "upload")
        backup_file.save(saved)
        with open(saved, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if not backup_crypto.is_encrypted(saved):
            return saved, False, digest
        if not passphrase:
            raise backup_crypto.BackupCryptoError("This backup is encrypted. Enter its passphrase.")
        plain = os.path.join(temp_dir, "backup.zip")
        backup_crypto.decrypt_file(saved, plain, passphrase)
        os.remove(saved)
        return plain, True, digest

    async def inspect_backup(self, backup_file, passphrase: Optional[str] = None) -> Dict[str, Any]:
        """Inspect backup archive and return metadata"""

        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()

        try:
            temp_file, encrypted, _ = self._open_upload(backup_file, temp_dir, passphrase)

            with zipfile.ZipFile(temp_file, 'r') as zipf:
                archive_limits.check_zip(zipf.infolist(), archive_limits.BACKUP_LIMITS)
                # Read metadata
                if "metadata.json" not in zipf.namelist():
                    raise Exception("Invalid backup file: missing metadata.json")

                metadata_content = zipf.read("metadata.json").decode('utf-8')
                metadata = json.loads(metadata_content)

                # Add file list from archive
                files_in_archive = [name for name in zipf.namelist() if name != "metadata.json"]
                metadata["files_in_archive"] = files_in_archive
                metadata["encrypted"] = encrypted

                return metadata

        except zipfile.BadZipFile:
            raise Exception("Invalid backup file: not a valid zip archive")
        except json.JSONDecodeError:
            raise Exception("Invalid backup file: corrupted metadata")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def preview_restore(
        self,
        backup_file,
        restore_include_patterns: Optional[List[str]] = None,
        restore_exclude_patterns: Optional[List[str]] = None,
        overwrite_policy: str = "overwrite",
        clean_before_restore: bool = False,
        user_edited_metadata: Optional[Dict[str, Any]] = None,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview which files would be restored based on patterns"""

        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()

        files_to_restore = []
        skipped_files = []

        try:
            temp_file, encrypted, _ = self._open_upload(backup_file, temp_dir, passphrase)

            with zipfile.ZipFile(temp_file, 'r') as zipf:
                archive_limits.check_zip(zipf.infolist(), archive_limits.BACKUP_LIMITS)
                # Read backup metadata from archive
                original_backup_metadata = {}
                if "metadata.json" in zipf.namelist():
                    metadata_content = zipf.read("metadata.json").decode('utf-8')
                    original_backup_metadata = json.loads(metadata_content)

                # Use user-edited metadata if provided, otherwise fall back to original
                backup_metadata = user_edited_metadata if user_edited_metadata else original_backup_metadata

                # Get files from archive (excluding metadata files)
                #
                # database.json is excluded here because it is not a file this
                # instance has. It is restored separately below, into the
                # database rather than onto the volume.
                archive_files = [name for name in zipf.namelist()
                                 if name not in ["metadata.json", "checksums.json",
                                                 self.DATABASE_ENTRY]]

                # Create pathspec for restore patterns if provided
                restore_spec = None
                if restore_include_patterns or restore_exclude_patterns:
                    pattern_lines = []
                    if restore_include_patterns:
                        # Translate patterns from backed up system to current system
                        translated_include_patterns = self._translate_patterns(restore_include_patterns, original_backup_metadata)
                        for pattern in translated_include_patterns:
                            # Remove leading slash for pathspec matching
                            pattern_lines.append(pattern.lstrip('/'))
                    if restore_exclude_patterns:
                        # Translate patterns from backed up system to current system
                        translated_exclude_patterns = self._translate_patterns(restore_exclude_patterns, original_backup_metadata)
                        for pattern in translated_exclude_patterns:
                            # Remove leading slash for pathspec matching
                            pattern_lines.append(f"!{pattern.lstrip('/')}")

                    if pattern_lines:
                        from pathspec import PathSpec
                        restore_spec = PathSpec.from_lines("gitignore", pattern_lines)

                # Process each file in archive
                budget = archive_limits.Budget(archive_limits.BACKUP_LIMITS)
                for archive_path in archive_files:
                    # Archive path is already the correct relative path (e.g., "eagent/tmp/settings.json")
                    original_path = archive_path

                    # Translate path from backed up system to current system,
                    # refusing anything that would land outside usr/. Before the
                    # pattern check: the patterns came from the archive too.
                    try:
                        target_path = self._translate_restore_path(
                            zipf.getinfo(archive_path), original_backup_metadata
                        )
                    except ValueError as refusal:
                        skipped_files.append({
                            "archive_path": archive_path,
                            "original_path": original_path,
                            "reason": "refused",
                            "detail": str(refusal),
                        })
                        continue

                    # For pattern matching, we need to use the translated path (current system)
                    # so that patterns like "/home/rafael/eagent/data/**" can match files correctly
                    translated_path_for_matching = target_path.lstrip('/')

                    # Check if file matches restore patterns
                    if restore_spec and not restore_spec.match_file(translated_path_for_matching):
                        skipped_files.append({
                            "archive_path": archive_path,
                            "original_path": original_path,
                            "reason": "not_matched_by_pattern"
                        })
                        continue

                    # Check file conflict policy for existing files
                    if os.path.exists(target_path):
                        if overwrite_policy == "skip":
                            skipped_files.append({
                                "archive_path": archive_path,
                                "original_path": original_path,
                                "reason": "file_exists_skip_policy"
                            })
                            continue

                    # File will be restored
                    files_to_restore.append({
                        "archive_path": archive_path,
                        "original_path": original_path,
                        "target_path": target_path,
                        "action": "restore"
                    })

                # Handle clean before restore if requested
                files_to_delete = []
                if clean_before_restore:
                    # Use user-edited metadata for clean operations so patterns from ACE editor are used
                    files_to_delete = await self._find_files_to_clean_with_user_metadata(backup_metadata, original_backup_metadata)

                # Combine delete and restore operations for preview
                all_operations = files_to_delete + files_to_restore

                return {
                    "files": all_operations,
                    "files_to_delete": files_to_delete,
                    "files_to_restore": files_to_restore,
                    "skipped_files": skipped_files,
                    "total_count": len(all_operations),
                    "delete_count": len(files_to_delete),
                    "restore_count": len(files_to_restore),
                    "skipped_count": len(skipped_files),
                    "backup_metadata": backup_metadata,  # Return user-edited metadata
                    "overwrite_policy": overwrite_policy,
                    "clean_before_restore": clean_before_restore,
                    "encrypted": encrypted,
                }

        except backup_crypto.BackupCryptoError:
            raise
        except zipfile.BadZipFile:
            raise Exception("Invalid backup file: not a valid zip archive")
        except json.JSONDecodeError:
            raise Exception("Invalid backup file: corrupted metadata")
        except Exception as e:
            raise Exception(f"Error previewing restore: {str(e)}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def restore_backup(
        self,
        backup_file,
        restore_include_patterns: Optional[List[str]] = None,
        restore_exclude_patterns: Optional[List[str]] = None,
        overwrite_policy: str = "overwrite",
        clean_before_restore: bool = False,
        user_edited_metadata: Optional[Dict[str, Any]] = None,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore files from backup archive"""

        allowed_origins = dotenv.get_dotenv_value("ALLOWED_ORIGINS", "")
        dotenv_path = os.path.abspath(dotenv.get_dotenv_file_path())

        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()

        restored_files = []
        skipped_files = []
        errors = []
        deleted_files = []

        try:
            # archive_sha256 says which archive this was, for the audit trail:
            # the file name is whatever the uploader called it.
            temp_file, encrypted, archive_sha256 = self._open_upload(backup_file, temp_dir, passphrase)

            with zipfile.ZipFile(temp_file, 'r') as zipf:
                archive_limits.check_zip(zipf.infolist(), archive_limits.BACKUP_LIMITS)
                # Read backup metadata from archive
                original_backup_metadata = {}
                if "metadata.json" in zipf.namelist():
                    metadata_content = zipf.read("metadata.json").decode('utf-8')
                    original_backup_metadata = json.loads(metadata_content)

                # Use user-edited metadata if provided, otherwise fall back to original
                backup_metadata = user_edited_metadata if user_edited_metadata else original_backup_metadata

                # Perform clean before restore if requested
                if clean_before_restore:
                    # Use user-edited metadata for clean operations so patterns from ACE editor are used
                    files_to_delete = await self._find_files_to_clean_with_user_metadata(backup_metadata, original_backup_metadata)
                    for delete_info in files_to_delete:
                        try:
                            real_path = delete_info["real_path"]
                            if os.path.exists(real_path) and os.path.isfile(real_path):
                                os.remove(real_path)
                                deleted_files.append({
                                    "path": delete_info["path"],
                                    "real_path": real_path,
                                    "action": "deleted",
                                    "reason": "clean_before_restore"
                                })
                        except Exception as e:
                            errors.append({
                                "path": delete_info["path"],
                                "real_path": delete_info.get("real_path", "unknown"),
                                "error": f"Failed to delete: {str(e)}"
                            })

                # Get files from archive (excluding metadata files)
                #
                # database.json is excluded here because it is not a file this
                # instance has. It is restored separately below, into the
                # database rather than onto the volume.
                archive_files = [name for name in zipf.namelist()
                                 if name not in ["metadata.json", "checksums.json",
                                                 self.DATABASE_ENTRY]]

                # Create pathspec for restore patterns if provided
                restore_spec = None
                if restore_include_patterns or restore_exclude_patterns:
                    pattern_lines = []
                    if restore_include_patterns:
                        # Translate patterns from backed up system to current system
                        translated_include_patterns = self._translate_patterns(restore_include_patterns, original_backup_metadata)
                        for pattern in translated_include_patterns:
                            # Remove leading slash for pathspec matching
                            pattern_lines.append(pattern.lstrip('/'))
                    if restore_exclude_patterns:
                        # Translate patterns from backed up system to current system
                        translated_exclude_patterns = self._translate_patterns(restore_exclude_patterns, original_backup_metadata)
                        for pattern in translated_exclude_patterns:
                            # Remove leading slash for pathspec matching
                            pattern_lines.append(f"!{pattern.lstrip('/')}")

                    if pattern_lines:
                        from pathspec import PathSpec
                        restore_spec = PathSpec.from_lines("gitignore", pattern_lines)

                # Process each file in archive
                budget = archive_limits.Budget(archive_limits.BACKUP_LIMITS)
                for archive_path in archive_files:
                    # Archive path is already the correct relative path (e.g., "eagent/tmp/settings.json")
                    original_path = archive_path

                    # Translate path from backed up system to current system,
                    # refusing anything that would land outside usr/. Before the
                    # pattern check: the patterns came from the archive too.
                    try:
                        target_path = self._translate_restore_path(
                            zipf.getinfo(archive_path), original_backup_metadata
                        )
                    except ValueError as refusal:
                        skipped_files.append({
                            "archive_path": archive_path,
                            "original_path": original_path,
                            "reason": "refused",
                            "detail": str(refusal),
                        })
                        continue

                    # For pattern matching, we need to use the translated path (current system)
                    # so that patterns like "/home/rafael/eagent/data/**" can match files correctly
                    translated_path_for_matching = target_path.lstrip('/')

                    # Check if file matches restore patterns
                    if restore_spec and not restore_spec.match_file(translated_path_for_matching):
                        skipped_files.append({
                            "archive_path": archive_path,
                            "original_path": original_path,
                            "reason": "not_matched_by_pattern"
                        })
                        continue

                    try:
                        # Handle overwrite policy
                        if os.path.exists(target_path):
                            if overwrite_policy == "skip":
                                skipped_files.append({
                                    "archive_path": archive_path,
                                    "original_path": original_path,
                                    "reason": "file_exists_skip_policy"
                                })
                                continue
                            elif overwrite_policy == "backup":
                                timestamp = Localization.get().now().strftime('%Y%m%d_%H%M%S')
                                backup_path = f"{target_path}.backup.{timestamp}"
                                shutil.move(target_path, backup_path)

                        # Create target directory if needed
                        target_dir = os.path.dirname(target_path)
                        if target_dir:
                            os.makedirs(target_dir, exist_ok=True)

                        # Extract file
                        # ponytail: checked, then opened -- a symlink swapped into usr/ in
                        # between is followed; walk with dir_fd + O_NOFOLLOW if usr/ ever
                        # gets an untrusted concurrent writer.
                        entry = zipf.getinfo(archive_path)
                        with zipf.open(entry) as source, open(target_path, 'wb') as target:
                            budget.copy(source, target, entry.compress_size)

                        if os.path.abspath(target_path) == dotenv_path:
                            dotenv.save_dotenv_value(
                                "ALLOWED_ORIGINS", allowed_origins, reload_env=False
                            )

                        restored_files.append({
                            "archive_path": archive_path,
                            "original_path": original_path,
                            "target_path": target_path,
                            "status": "restored"
                        })

                    except archive_limits.ArchiveTooLarge:
                        raise
                    except Exception as e:
                        errors.append({
                            "path": archive_path,
                            "original_path": original_path,
                            "error": str(e)
                        })

                # The database, after the files.
                #
                # Order matters and this is the safer one: if the database
                # restore fails, the archive is still intact and the operator can
                # try again. Doing it first would leave an instance whose
                # accounts came from the backup and whose conversations did not.
                database_restored: dict[str, int] = {}
                database_error = ""
                if self.DATABASE_ENTRY in zipf.namelist():
                    try:
                        payload = json.loads(zipf.read(self.DATABASE_ENTRY))
                        database_restored = db.load_data(payload)
                    except Exception as exc:  # noqa: BLE001
                        database_error = str(exc)
                        errors.append({
                            "path": self.DATABASE_ENTRY,
                            "error": f"the database was not restored: {exc}",
                        })
                else:
                    # A backup from before the database existed. Saying so is
                    # better than restoring the files and letting the operator
                    # discover their accounts are missing at the sign-in screen.
                    database_error = (
                        "This backup has no database in it. Files were restored; "
                        "accounts, settings and scheduled tasks were not, because "
                        "the backup predates them moving out of the volume."
                    )
                    errors.append({"path": self.DATABASE_ENTRY, "error": database_error})

                return {
                    "restored_files": restored_files,
                    "deleted_files": deleted_files,
                    "skipped_files": skipped_files,
                    "errors": errors,
                    "database_restored": database_restored,
                    "database_error": database_error,
                    "archive_sha256": archive_sha256,
                    "backup_metadata": backup_metadata,  # Return user-edited metadata
                    "clean_before_restore": clean_before_restore,
                    "encrypted": encrypted,
                }

        except backup_crypto.BackupCryptoError:
            raise
        except zipfile.BadZipFile:
            raise Exception("Invalid backup file: not a valid zip archive")
        except json.JSONDecodeError:
            raise Exception("Invalid backup file: corrupted metadata")
        except Exception as e:
            raise Exception(f"Error restoring backup: {str(e)}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _translate_restore_path(self, entry: zipfile.ZipInfo, backup_metadata: Dict[str, Any]) -> str:
        """Where an archive entry is restored to on this instance.

        The archive is untrusted input: its entry names, its metadata.json, and
        the restore patterns the interface copies out of that metadata. So this
        is the only gate between an entry and open(), and it does not ask the
        patterns. Raises ValueError, saying why, for anything that would not
        land strictly inside this instance's usr/.

        Args:
            entry: The archive entry
            backup_metadata: Metadata from the archive, for its eagent_root

        Returns:
            The entry's path on this instance
        """
        name = entry.filename
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise ValueError("symbolic link")
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError("absolute path or '..' component")

        # Without a recorded root there is nothing to translate from, and the
        # old answer -- write the name as an absolute path -- was the hole.
        environment_info = backup_metadata.get("environment_info") or {}
        backed_up_root = (environment_info.get("eagent_root") or "").strip("/")
        if not backed_up_root:
            raise ValueError("the archive does not record which instance it came from")
        if not name.startswith(backed_up_root + "/"):
            raise ValueError("outside the instance the backup was taken from")

        target = self.eagent_root.rstrip("/") + "/" + name[len(backed_up_root) + 1:]
        if not self._inside_usr(target):
            raise ValueError("resolves outside usr/")
        return target

    def _inside_usr(self, path: str) -> bool:
        """Whether path, with every symlink resolved, is strictly inside usr/.

        usr/ is everything restore may write or delete. The rest of the tree is
        code the server imports, and the account it runs as can write to it.
        """
        usr = os.path.realpath(os.path.join(self.eagent_root, "usr"))
        return os.path.realpath(path).startswith(usr + os.sep)

    async def _find_files_to_clean_with_user_metadata(self, user_metadata: Dict[str, Any], original_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find existing files that match patterns from user-edited metadata for clean operations"""
        # Use user-edited patterns for what to clean
        user_include_patterns = user_metadata.get("include_patterns", [])
        user_exclude_patterns = user_metadata.get("exclude_patterns", [])
        include_hidden = user_metadata.get("include_hidden", True)

        if not user_include_patterns:
            return []

        # Translate user-edited patterns from backed up system to current system
        # Use original metadata for path translation (environment_info)
        translated_include_patterns = self._translate_patterns(user_include_patterns, original_metadata)
        translated_exclude_patterns = self._translate_patterns(user_exclude_patterns, original_metadata)

        # Create metadata object for testing translated patterns
        metadata = {
            "include_patterns": translated_include_patterns,
            "exclude_patterns": translated_exclude_patterns,
            "include_hidden": include_hidden
        }

        # Find existing files that match the translated user-edited patterns
        try:
            existing_files = await self.test_patterns(metadata, max_files=None)

            # Convert to delete operations format
            files_to_delete = []
            for file_info in existing_files:
                # Never outside usr/, whatever the patterns say: they came from
                # the archive, and usr/ is all restore can put back.
                if os.path.exists(file_info["real_path"]) and self._inside_usr(file_info["real_path"]):
                    files_to_delete.append({
                        "path": file_info["path"],
                        "real_path": file_info["real_path"],
                        "action": "delete",
                        "reason": "clean_before_restore"
                    })

            return files_to_delete
        except Exception:
            # If pattern testing fails, return empty list to avoid breaking restore
            return []
