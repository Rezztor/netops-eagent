/**
 * Working-directory file operations.
 *
 * Two things about paths, both easy to get wrong and neither of which fails
 * loudly:
 *
 * `helpers/file_browser.py` uses `/` as its base, and returns each entry's path
 * *relative* to that base — so a listing yields `home/alice/...`, with no
 * leading slash, while every endpoint that takes a path wants an absolute one.
 * Some handlers re-add the slash themselves and some do not, so `absolute()`
 * normalises here rather than at each call site.
 *
 * `$WORK_DIR` is resolved by the backend to the container path. Asking for it by
 * name rather than computing a path keeps the frontend out of a decision that
 * depends on how the instance is deployed.
 */

import { apiAction, apiDownload, apiRequest, apiUpload } from "./client";

/** Ask the backend for the working directory rather than naming a path. */
export const WORK_DIR = "$WORK_DIR";

export interface FileEntry {
  name: string;
  /** Relative to `/`; use `absolute()` before sending it anywhere. */
  path: string;
  modified: string;
  type: string;
  size: number;
  is_dir: boolean;
}

export interface DirectoryListing {
  entries: FileEntry[];
  current_path: string;
  parent_path: string;
  error?: string;
}

/** Turn a listing-relative path into the absolute form the endpoints expect. */
export function absolute(path: string): string {
  if (!path) return path;
  return path.startsWith("/") || path.startsWith("$") ? path : `/${path}`;
}

/** The last segment, for display. */
export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const index = trimmed.lastIndexOf("/");
  return index >= 0 ? trimmed.slice(index + 1) : trimmed;
}

export async function listDirectory(path: string = WORK_DIR): Promise<DirectoryListing> {
  const query = new URLSearchParams({ path });
  const result = await apiRequest<{ data: DirectoryListing }>(
    `get_work_dir_files?${query.toString()}`,
    { method: "GET" },
  );
  return result.data;
}

/* ---------- create, upload ---------- */

export function uploadFiles(currentPath: string, files: File[]) {
  const form = new FormData();
  form.append("path", currentPath);
  // The handler reads `files[]`, not `files`.
  for (const file of files) form.append("files[]", file);
  return apiUpload<{ data?: DirectoryListing; failed?: unknown[] }>(
    "upload_work_dir_files",
    form,
  );
}

/**
 * Create a folder.
 *
 * Shares the rename endpoint and its `newName` parameter -- not `name`, which
 * is refused with "New name is required" rather than silently ignored, but is
 * an easy assumption to make from the action name.
 */
export function createFolder(currentPath: string, newName: string) {
  return apiAction<{ data?: DirectoryListing; error?: string }>("rename_work_dir_file", {
    body: { action: "create-folder", currentPath, parentPath: currentPath, newName },
  });
}

/* ---------- move, rename ---------- */

export function renameEntry(currentPath: string, path: string, newName: string) {
  return apiAction<{ data?: DirectoryListing; error?: string }>("rename_work_dir_file", {
    body: { action: "rename", currentPath, path: absolute(path), newName },
  });
}

export function moveEntries(currentPath: string, paths: string[], destinationPath: string) {
  return apiAction<{ data?: DirectoryListing; error?: string }>("rename_work_dir_file", {
    body: {
      action: "move",
      currentPath,
      paths: paths.map(absolute),
      destinationPath: absolute(destinationPath),
    },
  });
}

/* ---------- delete ---------- */

export function deleteEntry(currentPath: string, path: string) {
  return apiAction<{ data?: DirectoryListing; error?: string }>("delete_work_dir_file", {
    body: { currentPath, path: absolute(path) },
  });
}

export function deleteEntries(currentPath: string, paths: string[]) {
  return apiAction<{ data?: DirectoryListing; error?: string; failed?: unknown[] }>(
    "delete_work_dir_files",
    { body: { currentPath, paths: paths.map(absolute) } },
  );
}

/* ---------- archives ---------- */

export function extractArchive(currentPath: string, path: string) {
  return apiAction<{ data?: DirectoryListing; extracted_path?: string; error?: string }>(
    "extract_work_dir_archive",
    { body: { currentPath, path: absolute(path) } },
  );
}

/* ---------- read and write ---------- */

export interface FileContent {
  path?: string;
  content?: string;
  encoding?: string;
  [key: string]: unknown;
}

export function readFile(path: string) {
  const query = new URLSearchParams({ path: absolute(path) });
  return apiRequest<{ data?: FileContent; error?: string }>(
    `edit_work_dir_file?${query.toString()}`,
    { method: "GET" },
  );
}

export function writeFile(path: string, content: string) {
  return apiAction<{ data?: unknown; error?: string }>("edit_work_dir_file", {
    body: { path: absolute(path), content },
  });
}

export function fileInfo(path: string) {
  return apiRequest<Record<string, unknown>>("file_info", { body: { path: absolute(path) } });
}

/* ---------- download ---------- */

/**
 * Download one file, or several as one archive.
 *
 * Both go through `apiDownload` rather than a link: the endpoints require the
 * CSRF header, which a plain anchor cannot send.
 */
export function downloadFile(path: string): Promise<void> {
  const query = new URLSearchParams({ path: absolute(path) });
  return apiDownload(`download_work_dir_file?${query.toString()}`, {
    method: "GET",
    filename: basename(path),
  });
}

export function downloadFiles(currentPath: string, paths: string[]): Promise<void> {
  return apiDownload("download_work_dir_files", {
    method: "POST",
    body: { currentPath, paths: paths.map(absolute) },
    filename: "files.zip",
  });
}
