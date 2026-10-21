/** Save an export / template response as a file. Throws the server's error when there is nothing to save. */
export function saveExport(
  result: { ok: boolean; filename?: string; content_type?: string; content?: string; error?: string },
  fallbackType: string,
): string {
  if (!result.ok || !result.content || !result.filename) throw new Error(result.error || "Export failed.");
  const url = URL.createObjectURL(new Blob([result.content], { type: result.content_type || fallbackType }));
  const a = document.createElement("a");
  a.href = url;
  a.download = result.filename;
  a.click();
  URL.revokeObjectURL(url);
  return result.filename;
}
