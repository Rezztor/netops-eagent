/**
 * The working directory.
 *
 * Browse, upload, download, rename, move, delete, extract archives, and edit
 * text in place — the capability people notice losing within a minute.
 *
 * Everything is scoped per user by the backend: `usr/workdir/<user id>`. This
 * screen never constructs that path, it asks for `$WORK_DIR` and follows what
 * comes back, so the isolation cannot be bypassed by a client that computes the
 * wrong prefix.
 */

import { useCallback, useEffect, useState } from "react";

import {
  WORK_DIR,
  basename,
  createFolder,
  deleteEntries,
  downloadFile,
  downloadFiles,
  extractArchive,
  listDirectory,
  moveEntries,
  readFile,
  renameEntry,
  uploadFiles,
  writeFile,
  type DirectoryListing,
  type FileEntry,
} from "@/api/files";
import { fetchImageUrl } from "@/api/authoring";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  EmptyRow,
  Mono,
  ScopeNote,
  SearchInput,
  SortHeader,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useTableControls } from "@/lib/useTableControls";
import { useUrlState } from "@/lib/useUrlState";
import shell from "./shell.module.css";
import styles from "./files.module.css";

const ARCHIVE_SUFFIXES = [".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar"];
const IMAGES = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"]);
const EDITABLE = new Set([
  "txt", "md", "markdown", "json", "yaml", "yml", "csv", "log", "py", "js", "ts", "tsx",
  "jsx", "css", "html", "xml", "sh", "toml", "ini", "cfg", "conf", "env", "sql",
]);

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1).toLowerCase() : "";
}

function isArchive(name: string): boolean {
  const lower = name.toLowerCase();
  return ARCHIVE_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const fileSearch = (entry: FileEntry) => [entry.name, entry.type];

// Folders sort ahead of files on name, the way a file manager reads; the other
// columns compare their own value directly.
const FILE_COMPARATORS: Record<string, (a: FileEntry, b: FileEntry) => number> = {
  name: (a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name),
  size: (a, b) => a.size - b.size,
  modified: (a, b) => new Date(a.modified).getTime() - new Date(b.modified).getTime(),
  type: (a, b) =>
    (a.is_dir ? "folder" : a.type || "file").localeCompare(b.is_dir ? "folder" : b.type || "file"),
};

export function FilesScreen() {
  // The directory lives in the URL so a reload or a shared link reopens the
  // same folder rather than the working-directory root.
  const [path, setPath] = useUrlState("path", WORK_DIR);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState<{ path: string; text: string; dirty: boolean } | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const { confirm, prompt } = useConfirm();
  const toast = useToast();

  const load = useCallback(() => listDirectory(path), [path]);
  const { state, reload } = useAsync<DirectoryListing>(load, [path]);

  function navigate(next: string) {
    setPath(next);
    setSelected(new Set());
    setEditing(null);
  }

  async function run(action: () => Promise<unknown>, success?: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setSelected(new Set());
      reload();
      if (success) toast.success(success);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  // Upload reports per-file failures in `failed[]`; a blanket "done" would hide
  // the ones that did not land.
  async function doUpload(destination: string, files: File[]) {
    await run(async () => {
      const result = await uploadFiles(destination, files);
      const failed = result.failed ?? [];
      if (failed.length) {
        throw new Error(`${failed.length} of ${files.length} file(s) failed to upload.`);
      }
    }, `Uploaded ${files.length} file${files.length === 1 ? "" : "s"}.`);
  }

  async function promptNewFolder(destination: string) {
    const name = await prompt({
      title: "New folder",
      label: "Folder name",
      confirmLabel: "Create",
      validate: (value) => (value.trim() ? null : "A name is required."),
    });
    if (name?.trim()) void run(() => createFolder(destination, name.trim()), "Folder created.");
  }

  async function promptRename(entry: FileEntry, destination: string) {
    const name = await prompt({
      title: `Rename ${entry.name}`,
      label: "New name",
      defaultValue: entry.name,
      confirmLabel: "Rename",
      validate: (value) => (value.trim() ? null : "A name is required."),
    });
    if (name?.trim() && name.trim() !== entry.name) {
      void run(() => renameEntry(destination, entry.path, name.trim()), "Renamed.");
    }
  }

  async function promptMove(destination: string, paths: string[]) {
    const target = await prompt({
      title: `Move ${paths.length} item${paths.length === 1 ? "" : "s"}`,
      label: "Destination folder (absolute path)",
      defaultValue: destination,
      confirmLabel: "Move",
      validate: (value) => (value.trim() ? null : "A destination is required."),
    });
    if (target?.trim()) {
      void run(() => moveEntries(destination, paths, target.trim()), "Moved.");
    }
  }

  async function confirmDelete(destination: string, paths: string[], label: string) {
    const ok = await confirm({
      title: `Delete ${label}?`,
      body: "This cannot be undone.",
      danger: true,
      confirmLabel: "Delete",
    });
    if (ok) void run(() => deleteEntries(destination, paths), "Deleted.");
  }

  // Called before the early returns so hook order stays stable across states.
  const allEntries = state.status === "ready" ? state.data.entries ?? [] : [];
  const controls = useTableControls(allEntries, {
    search: fileSearch,
    comparators: FILE_COMPARATORS,
    initialSort: { key: "name", direction: "asc" },
  });

  if (state.status === "loading") return <CenteredMessage title="Loading files…" />;
  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Could not list the directory"
        detail={state.error.message}
        action={<Button variant="primary" onClick={reload}>Retry</Button>}
      />
    );
  }

  const listing = state.data;
  const current = listing.current_path;
  const entries = listing.entries ?? [];
  const rows = controls.rows;
  const selectedPaths = entries.filter((e) => selected.has(e.path)).map((e) => e.path);

  if (editing) {
    return (
      <FileEditor
        editing={editing}
        busy={busy}
        onChange={(text) => setEditing({ ...editing, text, dirty: true })}
        onClose={() => setEditing(null)}
        onSave={() =>
          run(async () => {
            const saved = editing.text;
            await writeFile(editing.path, saved);
            // Against the editor as it is now, not as it was when Save was
            // pressed: typing during the write must not be reverted, nor
            // marked saved.
            setEditing((now) => (now && now.path === editing.path ? { ...now, dirty: now.text !== saved } : now));
          })
        }
      />
    );
  }

  if (viewing) {
    return <ImageViewer path={viewing} onClose={() => setViewing(null)} onError={setError} />;
  }

  return (
    <div className={shell.stack}>
      {listing.error && (
        <ScopeNote tone="warn">
          <div>{listing.error}</div>
        </ScopeNote>
      )}
      {error && (
        <ScopeNote tone="crit">
          <div>{error}</div>
        </ScopeNote>
      )}

      <div
        className={dragging ? `${shell.stack} ${styles.dropzone}` : shell.stack}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDragging(false);
        }}
        onDrop={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(false);
          const files = Array.from(event.dataTransfer.files);
          if (files.length) void doUpload(current, files);
        }}
      >
        <Card>
          <Toolbar
            current={current}
            parent={listing.parent_path}
            busy={busy}
            selectedCount={selectedPaths.length}
            onNavigate={navigate}
            onUpload={(files) => void doUpload(current, files)}
            onNewFolder={() => void promptNewFolder(current)}
            onMoveSelected={() => void promptMove(current, selectedPaths)}
            onDownloadSelected={() => void run(() => downloadFiles(current, selectedPaths), "Download started.")}
            onDeleteSelected={() =>
              void confirmDelete(current, selectedPaths, `${selectedPaths.length} item${selectedPaths.length === 1 ? "" : "s"}`)
            }
            onRefresh={reload}
          />

          {entries.length > 0 && (
            <div style={{ padding: "0 0 var(--space-2)" }}>
              <SearchInput
                value={controls.query}
                onChange={controls.setQuery}
                placeholder="Filter this folder…"
                label="Filter files"
              />
            </div>
          )}

          {entries.length === 0 ? (
            <p className={styles.empty}>
              This folder is empty. Drop files here to upload.
            </p>
          ) : (
            <Table>
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={rows.length > 0 && rows.every((e) => selected.has(e.path))}
                      ref={(node) => {
                        // Indeterminate is not an attribute, only a property.
                        if (node) {
                          const shown = rows.filter((e) => selected.has(e.path)).length;
                          node.indeterminate = shown > 0 && shown < rows.length;
                        }
                      }}
                      onChange={(event) =>
                        setSelected(
                          event.target.checked ? new Set(rows.map((e) => e.path)) : new Set(),
                        )
                      }
                    />
                  </th>
                  <SortHeader columnKey="name" sort={controls.sort} onToggle={controls.toggleSort}>
                    Name
                  </SortHeader>
                  <SortHeader columnKey="type" sort={controls.sort} onToggle={controls.toggleSort}>
                    Type
                  </SortHeader>
                  <SortHeader columnKey="size" sort={controls.sort} onToggle={controls.toggleSort} align="right">
                    Size
                  </SortHeader>
                  <SortHeader columnKey="modified" sort={controls.sort} onToggle={controls.toggleSort}>
                    Modified
                  </SortHeader>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <EmptyRow colSpan={6}>No files match this filter.</EmptyRow>
                ) : (
                  rows.map((entry) => (
                    <EntryRow
                      key={entry.path}
                      entry={entry}
                      busy={busy}
                      checked={selected.has(entry.path)}
                      onToggle={(checked) =>
                        setSelected((current) => {
                          const next = new Set(current);
                          if (checked) next.add(entry.path);
                          else next.delete(entry.path);
                          return next;
                        })
                      }
                      onOpen={() => {
                        if (entry.is_dir) {
                          navigate(`/${entry.path}`);
                          return;
                        }
                        if (IMAGES.has(extensionOf(entry.name))) {
                          setViewing(entry.path);
                          return;
                        }
                        if (EDITABLE.has(extensionOf(entry.name))) {
                          void run(async () => {
                            const result = await readFile(entry.path);
                            if (result.error) throw new Error(result.error);
                            setEditing({
                              path: entry.path,
                              text: String(result.data?.content ?? ""),
                              dirty: false,
                            });
                          });
                          return;
                        }
                        void run(() => downloadFile(entry.path));
                      }}
                      onDownload={() => void run(() => downloadFile(entry.path), "Download started.")}
                      onRename={() => void promptRename(entry, current)}
                      onExtract={() => void run(() => extractArchive(current, entry.path), "Archive extracted.")}
                      onDelete={() => void confirmDelete(current, [entry.path], entry.name)}
                    />
                  ))
                )}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}

function Toolbar({
  current,
  parent,
  busy,
  selectedCount,
  onNavigate,
  onUpload,
  onNewFolder,
  onMoveSelected,
  onDownloadSelected,
  onDeleteSelected,
  onRefresh,
}: {
  current: string;
  parent: string;
  busy: boolean;
  selectedCount: number;
  onNavigate: (path: string) => void;
  onUpload: (files: File[]) => void;
  onNewFolder: () => void;
  onMoveSelected: () => void;
  onDownloadSelected: () => void;
  onDeleteSelected: () => void;
  onRefresh: () => void;
}) {
  const segments = current.split("/").filter(Boolean);

  return (
    <div className={styles.toolbar}>
      <nav className={styles.breadcrumb} aria-label="Breadcrumb">
        <button type="button" className={styles.crumb} onClick={() => onNavigate(WORK_DIR)}>
          Working directory
        </button>
        {segments.map((segment, index) => {
          const target = `/${segments.slice(0, index + 1).join("/")}`;
          const isLast = index === segments.length - 1;
          return (
            <span key={target} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span className={styles.sep}>/</span>
              <button
                type="button"
                className={isLast ? `${styles.crumb} ${styles.crumbCurrent}` : styles.crumb}
                disabled={isLast}
                onClick={() => onNavigate(target)}
              >
                {segment}
              </button>
            </span>
          );
        })}
      </nav>

      {parent && (
        <Button onClick={() => onNavigate(`/${parent.replace(/^\/+/, "")}`)} disabled={busy}>
          Up
        </Button>
      )}
      <label className="btn-file">
        <input
          type="file"
          multiple
          className="visually-hidden"
          aria-label="Upload files"
          disabled={busy}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length) onUpload(files);
            event.target.value = "";
          }}
        />
        <Button
          disabled={busy}
          onClick={(event) => {
            const input = (event.currentTarget.parentElement as HTMLElement | null)?.querySelector("input");
            (input as HTMLInputElement | null)?.click();
          }}
        >
          Upload
        </Button>
      </label>
      <Button disabled={busy} onClick={onNewFolder}>
        New folder
      </Button>
      {selectedCount > 0 && (
        <>
          <Tag tone="accent">{selectedCount} selected</Tag>
          <Button disabled={busy} onClick={onDownloadSelected}>
            Download
          </Button>
          <Button disabled={busy} onClick={onMoveSelected}>
            Move
          </Button>
          <Button variant="danger" disabled={busy} onClick={onDeleteSelected}>
            Delete
          </Button>
        </>
      )}
      <Button disabled={busy} onClick={onRefresh}>
        Refresh
      </Button>
    </div>
  );
}

function EntryRow({
  entry,
  busy,
  checked,
  onToggle,
  onOpen,
  onDownload,
  onRename,
  onExtract,
  onDelete,
}: {
  entry: FileEntry;
  busy: boolean;
  checked: boolean;
  onToggle: (checked: boolean) => void;
  onOpen: () => void;
  onDownload: () => void;
  onRename: () => void;
  onExtract: () => void;
  onDelete: () => void;
}) {
  return (
    <tr className={styles.row}>
      <td>
        <input
          type="checkbox"
          checked={checked}
          aria-label={`Select ${entry.name}`}
          onChange={(event) => onToggle(event.target.checked)}
        />
      </td>
      <td>
        <div className={styles.rowName}>
          <svg
            className={entry.is_dir ? `${styles.icon} ${styles.iconFolder}` : styles.icon}
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden="true"
          >
            {entry.is_dir ? (
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            ) : (
              <>
                <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                <path d="M13 2v7h7" />
              </>
            )}
          </svg>
          <button type="button" className={styles.nameButton} onClick={onOpen}>
            {entry.name}
          </button>
        </div>
      </td>
      <td>
        <Tag tone="muted">{entry.is_dir ? "folder" : entry.type || "file"}</Tag>
      </td>
      <td className={styles.numeric} style={{ textAlign: "right" }}>
        {entry.is_dir ? "—" : formatBytes(entry.size)}
      </td>
      <td className={styles.numeric}>
        {entry.modified ? <DateTime value={entry.modified} /> : "—"}
      </td>
      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        {!entry.is_dir && (
          <Button disabled={busy} onClick={onDownload}>
            Download
          </Button>
        )}{" "}
        {isArchive(entry.name) && (
          <>
            <Button disabled={busy} onClick={onExtract} title="Extract into this folder">
              Extract
            </Button>{" "}
          </>
        )}
        <Button disabled={busy} onClick={onRename}>
          Rename
        </Button>{" "}
        <Button variant="danger" disabled={busy} onClick={onDelete}>
          Delete
        </Button>
      </td>
    </tr>
  );
}

function FileEditor({
  editing,
  busy,
  onChange,
  onClose,
  onSave,
}: {
  editing: { path: string; text: string; dirty: boolean };
  busy: boolean;
  onChange: (text: string) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const { confirm } = useConfirm();
  return (
    <div className={shell.stack}>
      <Card
        title={basename(editing.path)}
        description={<Mono>{editing.path}</Mono>}
        actions={editing.dirty ? <Tag tone="warn">unsaved</Tag> : <Tag tone="ok">saved</Tag>}
      >
        <div style={{ padding: "var(--space-3) 0" }}>
          <textarea
            className={styles.editor}
            value={editing.text}
            spellCheck={false}
            aria-label={`Contents of ${basename(editing.path)}`}
            onChange={(event) => onChange(event.target.value)}
          />
          <div className={styles.editorBar}>
            <Button
              onClick={async () => {
                if (editing.dirty) {
                  const ok = await confirm({
                    title: "Discard unsaved changes?",
                    body: "Your edits to this file will be lost.",
                    danger: true,
                    confirmLabel: "Discard",
                  });
                  if (!ok) return;
                }
                onClose();
              }}
            >
              Back to files
            </Button>
            <div style={{ flex: 1 }} />
            <Button variant="primary" disabled={busy || !editing.dirty} onClick={onSave}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}


/**
 * Preview an image.
 *
 * The bytes come through the API rather than a direct `<img src>`, because the
 * endpoint requires the CSRF header. The object URL is revoked when the viewer
 * closes; leaving it would hold the blob for the life of the page.
 */
function ImageViewer({
  path,
  onClose,
  onError,
}: {
  path: string;
  onClose: () => void;
  onError: (message: string) => void;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    fetchImageUrl(path)
      .then((objectUrl) => {
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        revoked = objectUrl;
        setUrl(objectUrl);
      })
      .catch((caught: unknown) => {
        if (!cancelled) onError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [path, onError]);

  return (
    <div className={shell.stack}>
      <Card
        title={basename(path)}
        description={<Mono>{path}</Mono>}
        actions={<Button onClick={onClose}>Back to files</Button>}
      >
        <div style={{ padding: "var(--space-3) 0", textAlign: "center" }}>
          {url ? (
            <img
              src={url}
              alt={basename(path)}
              style={{ maxWidth: "100%", maxHeight: "60vh", borderRadius: "var(--radius-sm)" }}
            />
          ) : (
            <span style={{ color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>Loading…</span>
          )}
        </div>
      </Card>
    </div>
  );
}
