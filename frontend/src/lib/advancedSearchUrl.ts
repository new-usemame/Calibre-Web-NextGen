import type { AdvancedSearchParams } from './api.ts';

/*
 * Advanced-search criteria live in the URL (#2211). Kept only in component
 * state, a query died with the component: opening a result and coming back,
 * or reloading the tab to pick up edits made elsewhere, returned an empty
 * form. The URL is what survives a remount, a reload, a back link and a
 * bookmark, so it is the one place the submitted query is read from.
 */

const TEXT_KEYS = ['title', 'authors', 'publisher', 'comments'] as const;
const DATE_KEYS = ['publishstart', 'publishend'] as const;
const RATING_KEYS = ['rating_low', 'rating_high'] as const;
const ID_LIST_KEYS = [
  'include_tag', 'exclude_tag',
  'include_serie', 'exclude_serie',
  'include_language', 'exclude_language',
] as const;
const FORMAT_LIST_KEYS = ['include_extension', 'exclude_extension'] as const;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const RATING_RE = /^[1-5]$/;

/** Serialize submitted criteria to a query string (no leading `?`). Empty
 *  fields and the "any read status" default are omitted, so an empty search
 *  serializes to ''. */
export function advancedSearchToQuery(params: AdvancedSearchParams): string {
  const out = new URLSearchParams();
  for (const key of [...TEXT_KEYS, ...DATE_KEYS, ...RATING_KEYS]) {
    const value = (params[key] ?? '').trim();
    if (value) out.set(key, value);
  }
  if (params.read_status === 'read' || params.read_status === 'unread') {
    out.set('read_status', params.read_status);
  }
  for (const key of [...ID_LIST_KEYS, ...FORMAT_LIST_KEYS]) {
    for (const value of params[key] ?? []) out.append(key, String(value));
  }
  return out.toString();
}

/** Read criteria back from a query string. Returns null when the URL carries
 *  no criteria, so a bare /search opens on an empty form with no results.
 *  Values the form cannot represent (an unknown read status, a rating outside
 *  1-5, a malformed date) are dropped rather than sent to the server. */
export function advancedSearchFromQuery(search: string): AdvancedSearchParams | null {
  const query = new URLSearchParams(search);
  const params: AdvancedSearchParams = {};
  let found = false;

  for (const key of TEXT_KEYS) {
    const value = query.get(key)?.trim();
    if (value) { params[key] = value; found = true; }
  }
  for (const key of DATE_KEYS) {
    const value = query.get(key);
    if (value && DATE_RE.test(value)) { params[key] = value; found = true; }
  }
  for (const key of RATING_KEYS) {
    const value = query.get(key);
    if (value && RATING_RE.test(value)) { params[key] = value; found = true; }
  }
  const readStatus = query.get('read_status');
  if (readStatus === 'read' || readStatus === 'unread') {
    params.read_status = readStatus;
    found = true;
  }
  // Tag, series and language ids are numeric; restore them as numbers so the
  // posted body matches what the pickers produce.
  for (const key of ID_LIST_KEYS) {
    const values = query.getAll(key).filter(Boolean).map((v) => (/^\d+$/.test(v) ? Number(v) : v));
    if (values.length) { params[key] = values; found = true; }
  }
  for (const key of FORMAT_LIST_KEYS) {
    const values = query.getAll(key).filter(Boolean);
    if (values.length) { params[key] = values; found = true; }
  }
  return found ? params : null;
}
