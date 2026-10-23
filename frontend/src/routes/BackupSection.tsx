/**
 * Backup and restore.
 *
 * Restore is the most destructive action in the product: it writes over the
 * working directory, every user's chats and memory, and the settings document.
 * With "remove files not in the backup" it also deletes anything created since
 * the archive was taken.
 *
 * So the flow is deliberately three steps rather than one button. Choose an
 * archive, read what it contains, then see exactly how many files would be
 * written and how many deleted — and only then is Restore enabled, with the
 * delete count repeated in the confirmation. The point is that the blast radius
 * is visible before the decision, not discoverable after it.
 */

import { useState } from "react";

import {
  createBackup,
  inspectBackup,
  previewRestore,
  restoreBackup,
  type BackupDefaults,
  type BackupInspection,
  type OverwritePolicy,
  type RestorePreview,
} from "@/api/operations";
import { Button, Card, Mono, Row, ScopeNote, Select, Tag, TextInput, useConfirm } from "@/components";

/** Shortest passphrase the server accepts for a new backup. */
export const MIN_PASSPHRASE = 12;

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** One glob per line, trimmed, blanks dropped -- the editable form of a scope. */
function parsePatterns(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

const patternAreaStyle: React.CSSProperties = {
  width: "100%",
  minWidth: 320,
  minHeight: 96,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--text-xs)",
  padding: "var(--space-2)",
  border: "1px solid var(--line)",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface)",
  color: "var(--ink)",
  resize: "vertical",
};

export function BackupSection({
  defaults,
  onError,
  onNotice,
}: {
  defaults: BackupDefaults;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  return (
    <>
      <CreateBackup defaults={defaults} onError={onError} onNotice={onNotice} />
      <RestoreBackup onError={onError} onNotice={onNotice} />
    </>
  );
}

function CreateBackup({
  defaults,
  onError,
  onNotice,
}: {
  defaults: BackupDefaults;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [name, setName] = useState(String(defaults.metadata.backup_name || "rezztor-eagent-backup"));
  const [include, setInclude] = useState(defaults.default_patterns.include_patterns.join("\n"));
  const [exclude, setExclude] = useState(defaults.default_patterns.exclude_patterns.join("\n"));
  const [passphrase, setPassphrase] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);

  const includePatterns = parsePatterns(include);
  const excludePatterns = parsePatterns(exclude);
  const edited =
    include !== defaults.default_patterns.include_patterns.join("\n") ||
    exclude !== defaults.default_patterns.exclude_patterns.join("\n");
  const invalid = includePatterns.length === 0;

  return (
    <Card
      title="Create a backup"
      description="Everything durable lives under usr/: settings, per-user chats, memory and files."
      actions={
        edited ? (
          <Button
            onClick={() => {
              setInclude(defaults.default_patterns.include_patterns.join("\n"));
              setExclude(defaults.default_patterns.exclude_patterns.join("\n"));
            }}
          >
            Reset to defaults
          </Button>
        ) : undefined
      }
    >
      <Row label="Archive name" help="Downloaded to this browser, not stored on the instance.">
        <TextInput value={name} aria-label="Archive name" onChange={(e) => setName(e.target.value)} />
      </Row>
      <Row
        label="Included"
        help={
          invalid
            ? "At least one include pattern is required."
            : "One glob per line. The scope the archive is built from."
        }
      >
        <textarea
          value={include}
          aria-label="Include patterns"
          spellCheck={false}
          style={{ ...patternAreaStyle, ...(invalid ? { borderColor: "var(--crit)" } : {}) }}
          onChange={(event) => setInclude(event.target.value)}
        />
      </Row>
      <Row label="Excluded" help="One glob per line. Removed from the included set.">
        <textarea
          value={exclude}
          aria-label="Exclude patterns"
          spellCheck={false}
          style={patternAreaStyle}
          onChange={(event) => setExclude(event.target.value)}
        />
      </Row>
      <ScopeNote tone="warn">
        <div>
          <strong>This file holds the instance's keys, every account's password hash and every
          user's data.</strong> It is encrypted with the passphrase below. The passphrase cannot be
          recovered: without it the backup cannot be restored. Keep it somewhere other than
          next to the file.
        </div>
      </ScopeNote>
      <Row label="Passphrase" help={`At least ${MIN_PASSPHRASE} characters. Not stored on the instance.`}>
        <TextInput
          type="password"
          value={passphrase}
          aria-label="Backup passphrase"
          autoComplete="new-password"
          onChange={(e) => setPassphrase(e.target.value)}
        />
      </Row>
      <Row label="Repeat passphrase">
        <TextInput
          type="password"
          value={repeat}
          aria-label="Repeat backup passphrase"
          autoComplete="new-password"
          onChange={(e) => setRepeat(e.target.value)}
        />
      </Row>
      <Row
        label=""
        help="Large instances take a while; the download starts when the archive is finished."
      >
        <Button
          variant="primary"
          // The include-patterns rule beside this one already says itself, in
          // help text and a red border. The archive name did not: clear the
          // prefilled value to type a new one, leave it blank, and the button
          // went dead with nothing saying the field was required.
          disabled={busy}
          onClick={async () => {
            const missing = !name.trim()
              ? "The archive needs a name."
              : invalid
                ? "At least one include pattern is required."
                : passphrase.length < MIN_PASSPHRASE
                  ? `The passphrase must be at least ${MIN_PASSPHRASE} characters.`
                  : passphrase !== repeat
                    ? "The two passphrases are not the same."
                    : null;
            if (missing) {
              onError(missing);
              return;
            }
            setBusy(true);
            try {
              await createBackup(
                name.trim(),
                includePatterns,
                excludePatterns,
                Boolean(defaults.metadata.include_hidden),
                passphrase,
              );
              setPassphrase("");
              setRepeat("");
              onNotice("Backup downloaded. Keep the passphrase: it cannot be recovered.");
            } catch (caught) {
              onError(caught instanceof Error ? caught.message : String(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Building…" : "Create and download"}
        </Button>
      </Row>
    </Card>
  );
}

function RestoreBackup({
  onError,
  onNotice,
}: {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const { confirm } = useConfirm();
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<BackupInspection | null>(null);
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [policy, setPolicy] = useState<OverwritePolicy>("overwrite");
  const [clean, setClean] = useState(false);
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ restored: number; deleted: number } | null>(null);

  // Anything that changes the outcome invalidates the preview: restoring
  // against a preview of different settings is the failure this flow exists to
  // prevent.
  function invalidate() {
    setPreview(null);
  }

  const deleteCount = preview?.delete_count ?? 0;
  const restoreCount = preview?.restore_count ?? 0;

  return (
    <Card
      title="Restore from a backup"
      description="Writes over settings, chats, memory and files for every user on this instance."
      danger
    >
      <Row label="Archive" help="Nothing is read from it until you choose Inspect.">
        <input
          type="file"
          accept=".rzbak,.zip"
          aria-label="Backup archive"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setInspection(null);
            setPassphrase("");
            invalidate();
          }}
        />
      </Row>

      {file && !inspection && (
        <Row label="Passphrase" help="Needed for an encrypted backup. Not stored, never sent anywhere but this instance.">
          <TextInput
            type="password"
            value={passphrase}
            aria-label="Backup passphrase"
            autoComplete="off"
            onChange={(e) => setPassphrase(e.target.value)}
          />
        </Row>
      )}

      {file && !inspection && (
        <Row label="" help="Reads the manifest. Changes nothing.">
          <Button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await inspectBackup(file, passphrase);
                if (!result.success) throw new Error(result.error || "Could not read the archive.");
                setInspection(result);
              } catch (caught) {
                onError(caught instanceof Error ? caught.message : String(caught));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Reading…" : "Inspect"}
          </Button>
        </Row>
      )}

      {inspection && inspection.encrypted === false && (
        <ScopeNote tone="warn">
          <div>
            <strong>This backup was not protected.</strong> It was made before backups were
            encrypted, so anyone who has the file can read the keys, password hashes and user data
            in it. It can still be restored; take a new backup, which is encrypted, afterwards.
          </div>
        </ScopeNote>
      )}

      {inspection && (
        <>
          <Row label="Contains">
            <div style={{ textAlign: "right" }}>
              <div>{inspection.total_files ?? 0} files · {formatBytes(inspection.backup_size ?? 0)}</div>
              <div style={{ color: "var(--ink-3)", fontSize: "var(--text-xs)" }}>
                {inspection.backup_name || "unnamed"} · {inspection.timestamp || "no timestamp"}
              </div>
            </div>
          </Row>
          <Row
            label="Taken from version"
            help={
              inspection.eagent_version && inspection.eagent_version !== "unknown"
                ? "Restoring across versions can reintroduce settings this build no longer understands."
                : undefined
            }
          >
            <Mono>{inspection.eagent_version || "unknown"}</Mono>
          </Row>

          <Row label="Existing files" help="What happens when the archive also contains a file that is already here.">
            <Select
              value={policy}
              aria-label="Overwrite policy"
              onChange={(event) => {
                setPolicy(event.target.value as OverwritePolicy);
                invalidate();
              }}
            >
              <option value="overwrite">Replace them</option>
              <option value="skip">Keep what is on disk</option>
              <option value="backup">Move the current one aside first</option>
            </Select>
          </Row>

          <Row
            label="Remove files not in the backup"
            help="This is the destructive option: anything created since the archive was taken is deleted."
          >
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={clean}
                onChange={(event) => {
                  setClean(event.target.checked);
                  invalidate();
                }}
              />
              <span style={{ fontSize: "var(--text-sm)", color: clean ? "var(--crit)" : "var(--ink-3)" }}>
                {clean ? "will delete" : "off"}
              </span>
            </label>
          </Row>

          <Row label="" help="Counts exactly what would change. Still changes nothing.">
            <Button
              disabled={busy}
              onClick={async () => {
                if (!file) return;
                setBusy(true);
                try {
                  const result = await previewRestore(
                    file,
                    inspection.metadata ?? {},
                    policy,
                    clean,
                    passphrase,
                  );
                  if (!result.success) throw new Error(result.error || "Could not preview.");
                  setPreview(result);
                } catch (caught) {
                  onError(caught instanceof Error ? caught.message : String(caught));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Checking…" : preview ? "Re-check" : "Preview changes"}
            </Button>
          </Row>
        </>
      )}

      {preview && (
        <>
          <Row label="Would write">
            <Tag tone="accent">{restoreCount} files</Tag>
          </Row>
          <Row
            label="Would delete"
            help={deleteCount > 0 ? "These are not in the archive and will not be recoverable." : undefined}
          >
            <Tag tone={deleteCount > 0 ? "crit" : "muted"}>{deleteCount} files</Tag>
          </Row>

          {deleteCount > 0 && (
            <ScopeNote tone="crit">
              <div>
                <strong>{deleteCount} files will be deleted.</strong> They exist on
                this instance and are not in the archive, so restoring removes
                them permanently. Untick "remove files not in the backup" to keep
                them.
              </div>
            </ScopeNote>
          )}

          <Row label="" help="Restarts nothing; chats are reloaded once it finishes.">
            <Button
              variant="danger"
              disabled={busy}
              onClick={async () => {
                if (!file) return;
                const ok = await confirm({
                  title: "Restore this backup?",
                  body:
                    deleteCount > 0
                      ? `${restoreCount} files will be written and ${deleteCount} deleted permanently. The deleted files are not in the archive and cannot be recovered.`
                      : `${restoreCount} files will be written over.`,
                  danger: true,
                  confirmLabel: "Restore",
                });
                if (!ok) return;
                setBusy(true);
                try {
                  const result = await restoreBackup(
                    file,
                    inspection?.metadata ?? {},
                    policy,
                    clean,
                    passphrase,
                  );
                  if (!result.success) throw new Error(result.error || "Restore failed.");
                  const restored = result.restored_files?.length ?? 0;
                  const deleted = result.deleted_files?.length ?? 0;
                  onNotice(`Restored ${restored} files${deleted ? `, deleted ${deleted}` : ""}.`);
                  setPreview(null);
                  setPassphrase("");
                  setDone({ restored, deleted });
                } catch (caught) {
                  onError(caught instanceof Error ? caught.message : String(caught));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Restoring…" : "Restore"}
            </Button>
          </Row>
        </>
      )}

      {done && (
        <ScopeNote tone="warn">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
            <span>
              <strong>Restored {done.restored} files{done.deleted ? `, deleted ${done.deleted}` : ""}.</strong>{" "}
              The running app still holds the pre-restore settings and chats in memory. Reload to
              read the restored state from disk.
            </span>
            <Button variant="primary" onClick={() => window.location.reload()}>
              Reload now
            </Button>
          </div>
        </ScopeNote>
      )}
    </Card>
  );
}
