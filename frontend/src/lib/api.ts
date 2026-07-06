/* Typed fetch helpers — same-origin, credentials included. */

declare global {
  interface Window { __CWNG_PREFIX__?: string; }
}

// Reverse-proxy mount prefix (e.g. "/cwa" when the app is served at
// https://host/cwa/). The server injects window.__CWNG_PREFIX__ into the SPA
// shell from request.script_root; fall back to deriving it from the current
// path (strip the "/app…" SPA segment) so a stale cached shell still works.
// Empty string when mounted at the domain root — the common case.
function _detectPrefix(): string {
  if (typeof window === 'undefined') return '';
  const injected = window.__CWNG_PREFIX__;
  if (injected !== undefined && injected !== null) return injected.replace(/\/+$/, '');
  const derived = window.location.pathname.replace(/\/app(\/.*)?$/, '');
  return derived.replace(/\/+$/, '');
}

export const BASE_PREFIX: string = _detectPrefix();

/** Prefix an app-internal API/route path with the reverse-proxy mount prefix. */
export function apiUrl(path: string): string {
  return BASE_PREFIX + path;
}

/** Prefix a server-generated resource URL (cover/download/read) with the mount
 *  prefix. Leaves absolute (http/https/protocol-relative) and data: URLs — e.g.
 *  an external metadata provider's cover — untouched, and is idempotent: a value
 *  that already carries the prefix (e.g. a Flask url_for path returned by an
 *  apply endpoint) is not prefixed a second time. */
export function resourceUrl(u: string): string {
  if (/^(https?:)?\/\//i.test(u) || u.startsWith('data:')) return u;
  if (BASE_PREFIX && (u === BASE_PREFIX || u.startsWith(BASE_PREFIX + '/'))) return u;
  return BASE_PREFIX + u;
}

export interface ServerFeatures {
  hide_books: boolean;
  mail_configured: boolean;
  public_registration: boolean;
  anon_browse: boolean;
  kobo_sync: boolean;
}

export interface Me {
  id: number;
  name: string;
  locale: string;
  theme: string;
  role: Record<string, boolean>;
  /** Sidebar-entry visibility (#585) — {key: enabled}. Mirrors the classic UI's
   *  per-user/instance sidebar_view config. Absent on older servers → treat
   *  every entry as visible. */
  sidebar?: Record<string, boolean>;
  /** Saved per-user sidebar order (#585 v2) — list of entry keys. Absent/empty
   *  → the SPA default order. */
  sidebar_order?: string[];
  /** Custom profile picture (#668) — a `data:image/…;base64,…` URI set in the
   *  classic profile-pictures panel, or null when the user has none. Absent on
   *  older servers → treat as null (falls back to the neutral glyph). */
  avatar?: string | null;
  features?: ServerFeatures;
  instance_name?: string;
}

export interface Book {
  id: number;
  title: string;
  authors: string[];
  series: string | null;
  series_index: number | null;
  cover_url: string | null;
  formats: string[];
  read?: boolean;
  archived?: boolean;
}

export interface BookFormat {
  format: string;
  size_bytes: number;
  download_url: string;
  read_url: string;
}

/** A linked entity (author, series, tag, publisher, language). id is numeric
 *  for most entities and a string lang_code for languages. */
export interface EntityRef {
  id: number | string;
  name: string;
}

export interface BookDetail {
  id: number;
  title: string;
  authors: EntityRef[];
  series: EntityRef | null;
  series_index: string;
  cover_url: string | null;
  pubdate: string | null;
  description_html: string | null;
  tags: EntityRef[];
  languages: EntityRef[];
  publishers: EntityRef[];
  identifiers: { type: string; val: string; url: string | null; label: string }[];
  formats: BookFormat[];
  read: boolean;
  archived: boolean;
  favorited: boolean;
  hidden: boolean;
  /** Sync-driven "currently reading" tri-state (fork #634) — true when KOReader/
   *  Kobo reports the book as in progress (read_status IN_PROGRESS) and it isn't
   *  marked read. Distinct from `read`; matches the classic detail page marker. */
  in_progress: boolean;
  /** KOReader/Kobo synced reading progress as a percentage (0–100), or null when
   *  not synced. */
  kosync_progress: number | null;
}

export interface BooksPage {
  items: Book[];
  page: number;
  per_page: number;
  total: number;
}

/** One row in an entity-browse list, with how many books reference it. */
export interface EntityListItem extends EntityRef {
  count: number;
}

export interface EntityList {
  items: EntityListItem[];
}

export interface Shelf {
  id: number;
  name: string;
  is_public: boolean;
  is_owner: boolean;
  kobo_sync: boolean;
  count: number;
}

export interface ShelfDetail extends Shelf {
  items: Book[];
  page: number;
  per_page: number;
  total: number;
  can_edit: boolean;
}

export interface SearchOptions {
  tags: EntityRef[];
  series: EntityRef[];
  languages: EntityRef[];
  formats: string[];
}

export interface AdvancedSearchParams {
  title?: string;
  authors?: string;
  publisher?: string;
  comments?: string;
  read_status?: 'all' | 'read' | 'unread';
  publishstart?: string;
  publishend?: string;
  rating_high?: string;
  rating_low?: string;
  include_tag?: (string | number)[];
  exclude_tag?: (string | number)[];
  include_serie?: (string | number)[];
  exclude_serie?: (string | number)[];
  include_language?: (string | number)[];
  exclude_language?: (string | number)[];
  include_extension?: string[];
  exclude_extension?: string[];
  sort?: string;
}

export interface AdvSearchResult {
  items: Book[];
  page: number;
  per_page: number;
  total: number;
  criteria: string;
}

export interface AppPassword {
  id: number;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
}

export interface Account {
  name: string;
  email: string;
  kindle_mail: string;
  kindle_mail_subject: string;
  kobo_only_shelves_sync: boolean;
  opds_only_shelves_sync: boolean;
  locale: string;
  default_language: string;
  role: Record<string, boolean>;
  can_change_password: boolean;
  locales: { id: string; name: string }[];
  languages: { id: string; name: string }[];
  app_passwords: AppPassword[];
}

export interface ProfileUpdate {
  email?: string;
  kindle_mail?: string;
  kindle_mail_subject?: string;
  kobo_only_shelves_sync?: boolean;
  opds_only_shelves_sync?: boolean;
  locale?: string;
  default_language?: string;
}

export interface BookMetadata {
  id: number;
  title: string;
  authors: string;
  series: string;
  series_index: number | string;
  tags: string;
  publishers: string;
  languages: string;
  comments: string;
  rating: number;
  identifiers: { type: string; val: string }[];
  errors?: Record<string, string>;
}

export type MetadataUpdate = Partial<Omit<BookMetadata, 'id' | 'errors'>>;

export interface UploadResult {
  queued: string[];
  errors: { filename: string; error: string }[];
}

export interface AdminUser {
  id: number;
  name: string;
  email: string;
  kindle_mail: string;
  locale: string;
  default_language: string;
  is_guest: boolean;
  roles: Record<string, boolean>;
}

export interface OAuthProvider {
  id: number;
  name: string;
  url: string;
}

export interface AuthConfig {
  instance_name?: string;
  public_registration: boolean;
  register_email: boolean;
  mail_configured: boolean;
  standard_login_disabled: boolean;
  oauth_providers: OAuthProvider[];
  remote_login: boolean;
  remote_login_url: string;
}

export interface AboutInfo {
  counts: { books: number; authors: number; categories: number; series: number };
  versions: Record<string, string>;
}

export interface TaskItem {
  task_id: number | string;
  taskMessage: string;
  status?: string;
  progress: string;
  starttime?: string;
  runtime?: string;
  user: string;
  is_cancellable: boolean;
  stat: number;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

let _csrfCache: string | null = null;

export async function getCsrf(): Promise<string> {
  if (_csrfCache) return _csrfCache;
  const res = await fetch(apiUrl('/api/v1/auth/csrf'), { credentials: 'include' });
  if (!res.ok) throw new ApiError(res.status, 'Failed to fetch CSRF token');
  const data = await res.json() as { csrf_token: string };
  _csrfCache = data.csrf_token;
  return _csrfCache;
}

function clearCsrf() {
  _csrfCache = null;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { credentials: 'include' });
  if (!res.ok) {
    let msg = res.statusText;
    // API errors are shaped { error: { code, message } }; fall back to a bare
    // string error or the HTTP status text if the body isn't that shape.
    try {
      const d = await res.json() as { error?: string | { message?: string } };
      if (typeof d.error === 'string') msg = d.error;
      else if (d.error?.message) msg = d.error.message;
    } catch { /* non-JSON body — keep statusText */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> => {
    return fetch(apiUrl(path), {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  };

  let csrf = await getCsrf();
  let res = await doPost(csrf);

  // A stale/invalid CSRF token is rejected app-side as an HTML 400 (the global
  // error page), NOT as one of our JSON {error:{…}} envelopes. Only that case
  // warrants refreshing the token and replaying the request once. A JSON 400 is
  // one of our own validation errors (wrong password, bad email, …) and must
  // surface to the caller — replaying it would silently double-submit the
  // request (doubled backend work + audit entries). Discriminate on content-type.
  const isJson400 = (res.status === 400)
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf();
    res = await doPost(csrf);
  }

  if (!res.ok) {
    let msg = res.statusText;
    // API errors are shaped { error: { code, message } }; fall back to a bare
    // string error or the HTTP status text if the body isn't that shape.
    try {
      const d = await res.json() as { error?: string | { message?: string } };
      if (typeof d.error === 'string') msg = d.error;
      else if (d.error?.message) msg = d.error.message;
    } catch { /* non-JSON body — keep statusText */ }
    throw new ApiError(res.status, msg);
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

/** Form-encoded POST (application/x-www-form-urlencoded). Used to consume the
 *  legacy form endpoints (e.g. /metadata/search) directly, reusing their logic
 *  rather than duplicating it under /api/v1. Same CSRF-retry as apiPost. */
export async function apiPostForm<T>(path: string, fields: Record<string, string>): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> =>
    fetch(apiUrl(path), {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrf },
      body: new URLSearchParams(fields).toString(),
    });

  let csrf = await getCsrf();
  let res = await doPost(csrf);
  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf();
    res = await doPost(csrf);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = await res.json() as { error?: string | { message?: string } };
      if (typeof d.error === 'string') msg = d.error;
      else if (d.error?.message) msg = d.error.message;
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export interface MetaResult {
  title: string;
  authors: string[];
  cover: string;
  description?: string;
  series?: string | null;
  series_index?: number | null;
  publisher?: string | null;
  publishedDate?: string | null;
  rating?: number | null;
  tags?: string[];
  identifiers?: Record<string, string | number>;
  format?: string | null;
  source?: { id?: string; description?: string };
}

export interface MetaSearchResponse {
  results: MetaResult[];
  providers: { id: string; name: string; status: string; count: number; message: string }[];
}

/** Multipart POST (file upload). Mirrors apiPost's CSRF handling, but lets the
 *  browser set the multipart Content-Type + boundary (so we must NOT set it). */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> =>
    fetch(apiUrl(path), {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRFToken': csrf },
      body: formData,
    });

  let csrf = await getCsrf();
  let res = await doPost(csrf);

  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf();
    res = await doPost(csrf);
  }

  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = await res.json() as { error?: string | { message?: string } };
      if (typeof d.error === 'string') msg = d.error;
      else if (d.error?.message) msg = d.error.message;
    } catch { /* non-JSON body — keep statusText */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}
