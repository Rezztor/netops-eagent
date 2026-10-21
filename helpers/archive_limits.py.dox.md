# archive_limits.py DOX

## Purpose

- Bound how far an archive an account supplies may expand: member count, largest member, total uncompressed size, compression ratio (`Limits`, default 1 GiB / 20,000 / 512 MiB / 200:1; `BACKUP_LIMITS` for a whole-instance backup).
- Callers still refuse absolute paths, `..`, links and devices themselves; this module only adds the size checks.

## Ownership

- `check_zip(infos, limits)` refuses on the central directory before anything is written.
- `Budget.copy(src, dst, packed)` meters the bytes really written, so a header that lies still hits the cap. `extract_zip(archive, target, limits)` is check plus metered extraction.
- `check_tar(archive, packed, limits)` walks tar headers (open the tar again to extract); `check_sizes(sizes, packed, limits)` serves tar and the 7-Zip listing; `tree_size` is the post-extraction check for 7-Zip, which cannot be metered.
- Raises `ArchiveTooLarge` (a `ValueError`), so API handlers that catch `ValueError` report it to the caller.
- The ratio is judged only above 1 MiB of output, where it means something.

## Verification

- `pytest tests/test_archive_limits.py`; used by `api/extract_work_dir_archive.py`, `helpers/skills_import.py`, `helpers/backup.py`.

## Child DOX Index

No child DOX files.
