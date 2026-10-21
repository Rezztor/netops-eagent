# extract_work_dir_archive.py DOX

## Purpose

- Own the authenticated, CSRF-protected archive extraction endpoint for File Browser.
- Extract supported archives into a new sibling folder without overwriting existing content.

## Ownership

- `ExtractWorkDirArchive` receives a file `path` and optional listing `currentPath`.
- `extract_archive` validates the source, creates the destination, and removes partial output on failure.

## Runtime Contracts

- The archive `path` resolves against the caller's own File Browser root, through `download_work_dir_file.resolve_download_path`; anything outside it is refused.
- ZIP and TAR-family archives use the Python standard library; RAR, 7z, and single-file compression formats use the image `7zip` binary.
- Archive members must remain below the new destination and cannot be links or devices.
- The 7-Zip path refuses a link member from the `-slt` listing before `x` runs (`is_link_listing_line`: a non-empty `Symbolic Link`, `Hard Link`, `Link` or `Copy Link` property, or an `Attributes` value carrying the Windows reparse letter `L` or a Unix `l` mode), because a link 7-Zip created could carry a later member's write outside the target before any after-the-fact check. It then extracts into a private `.extract-*` staging folder beside the target, checks it for symlinks and total size, and moves it into place only then; the staging folder is removed either way. `tests/test_extract_7zip_links.py` covers this with a fake binary.
- After any format is extracted, `file_browser.check_account_quota()` runs; over the account's quota the new folder is removed and the error returned.
- An archive over the size limits (members, largest member, total, compression ratio; `helpers/archive_limits.py`) is refused with an error and the partial output removed.
- Successful extraction emits `workdir_file_mutation_after` and returns a refreshed file listing plus `extracted_path`.

## Verification

- Run `pytest tests/test_file_browser_archives.py tests/test_file_browser_navigation.py tests/test_extract_7zip_links.py tests/test_account_storage_quota.py tests/test_archive_limits.py`.
