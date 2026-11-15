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

/** Keep lookup mode on server-reader fallbacks, preserving source and fragments. */
export function withLookupMode(target: string, lookup: boolean): string {
  if (!lookup) return target;
  const hashIndex = target.indexOf('#');
  const pathAndQuery = hashIndex < 0 ? target : target.slice(0, hashIndex);
  const hash = hashIndex < 0 ? '' : target.slice(hashIndex);
  const queryIndex = pathAndQuery.indexOf('?');
  const path = queryIndex < 0 ? pathAndQuery : pathAndQuery.slice(0, queryIndex);
  const params = new URLSearchParams(queryIndex < 0 ? '' : pathAndQuery.slice(queryIndex + 1));
  params.set('lookup', '1');
  return `${path}?${params.toString()}${hash}`;
}

/**
 * Hand the page's own fragment to the bundled pdf.js viewer (#2537).
 *
 * pdf.js reads its viewer options (`page`, `zoom`, `search`, `nameddest`,
 * `pagemode`) from its own location hash, so `/app/view/7/pdf#page=12` only
 * opens on page 12 if the iframe URL carries that same `#page=12`.
 */
export function withViewerHash(target: string, hash: string): string {
  const hashIndex = target.indexOf('#');
  const base = hashIndex < 0 ? target : target.slice(0, hashIndex);
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash;
  return fragment ? `${base}#${fragment}` : base;
}
