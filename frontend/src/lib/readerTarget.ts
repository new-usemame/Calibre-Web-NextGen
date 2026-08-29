const SPA_READABLE = new Set(['epub', 'kepub']);
export const SERVER_READABLE_FORMATS = [
  'pdf', 'txt', 'djvu', 'djv', 'cbz', 'cbr', 'cbt',
  'mp3', 'mp4', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav',
] as const;
const SERVER_READABLE = new Set<string>(SERVER_READABLE_FORMATS);

export function getPrimaryReadTarget(
  id: number | string,
  formats: string[],
  canRead: boolean,
): string | null {
  if (!canRead) return null;
  const normalized = formats.map((format) => format.toLowerCase());
  if (normalized.some((format) => SPA_READABLE.has(format))) return `/read/${id}`;
  const fallback = normalized.find((format) => SERVER_READABLE.has(format));
  return fallback ? `/view/${id}/${fallback}` : null;
}

export function isReadableFormat(format: string): boolean {
  const normalized = format.toLowerCase();
  return SPA_READABLE.has(normalized) || SERVER_READABLE.has(normalized);
}

/**
 * Resolve the viewer-gated archive URL used by epub.js.
 *
 * `contentUrl` was added to the detail API together with the viewer/download
 * split. During a rolling deployment, the new SPA can briefly receive a payload
 * from an older worker that does not have that field yet. Deriving the
 * established `/show` route keeps reading available without ever falling back
 * to the download-gated URL.
 */
export function getReaderContentUrl(
  id: number | string,
  format: string,
  contentUrl?: string,
): string {
  return contentUrl || `/show/${id}/${format.toLowerCase()}`;
}
