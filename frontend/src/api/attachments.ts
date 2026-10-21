/**
 * Downloading a file the agent attached.
 *
 * The endpoint takes only the attachment id: the server resolves the owner from
 * the session, so there is nothing here that could ask for somebody else's file.
 */

import { apiDownload } from "./client";

export interface Attachment {
  id: string;
  name: string;
  bytes: number;
}

/** The attachment on a log entry, or null if it carries none. */
export function attachmentOf(kvps: Record<string, unknown> | null | undefined): Attachment | null {
  if (!kvps) return null;
  const id = typeof kvps.attachment_id === "string" ? kvps.attachment_id : "";
  const name = typeof kvps.attachment_name === "string" ? kvps.attachment_name : "";
  if (!id || !name) return null;
  const bytes = Number(kvps.attachment_bytes ?? 0);
  return { id, name, bytes: Number.isFinite(bytes) ? bytes : 0 };
}

export function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export function downloadAttachment(attachment: Attachment): Promise<void> {
  return apiDownload(
    `plugins/_ea_attachments/attachment_get?id=${encodeURIComponent(attachment.id)}`,
    { method: "GET", filename: attachment.name },
  );
}
