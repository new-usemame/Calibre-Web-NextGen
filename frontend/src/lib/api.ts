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
  /** #870 — the admin's "Sync Magic Shelves to Kobo" setting. A smart shelf's
   *  per-shelf mark is inert while this is off, so the SPA only offers the
   *  toggle when it can actually do something. Absent on older servers →
   *  treat as off. */
  kobo_sync_magic_shelves?: boolean;
}

export interface Me {
  id: number;
  name: string;
  locale: string;
  theme: string;
  /** #701 — UI font preset keys (see lib/fonts.ts). Absent on older servers. */
  ui_font_body?: string;
  ui_font_display?: string;
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
  /** #866 — the account's "Sync only selected shelves to Kobo" setting. The
   *  shelf page warns when a shelf is marked for Kobo sync while this is off,
   *  because the mark does nothing until it is on. Absent on older servers →
   *  stay quiet rather than warn wrongly. */
  kobo_only_shelves_sync?: boolean;
  features?: ServerFeatures;
  instance_name?: string;
  display?: {
    books_per_page: number;
    random_books: number;
  };
  /** Per-user catalog landing preferences (#498), persisted server-side. */
  catalog?: {
    default_filter: AdvancedSearchParams | null;
  };
}

export interface Book {
  id: number;
  title: string;
  authors: string[];
  series: string | null;
  series_index: number | null;
  cover_url: string | null;
  formats: string[];
  /** Tag names (#725) — powers the table view's Tags column. Absent on older
   *  servers that predate the list-item tags field → treat as no tags. */
  tags?: string[];
  date_added?: string | null;
  last_modified?: string | null;
  read?: boolean;
  archived?: boolean;
  /** Personal-library declutter state. Present on list items from current servers. */
  hidden?: boolean;
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

export interface CustomColumnValue {
  value: string | number | boolean | null;
  extra: string | number | null;
  value_html?: string;
}

export interface CustomColumn {
  id: number;
  label: string;
  name: string;
  datatype: string;
  is_multiple: boolean;
  values: CustomColumnValue[];
}

export interface BookDetail {
  id: number;
  title: string;
  authors: EntityRef[];
  series: EntityRef | null;
  series_index: string;
  /** Calibre rating on a 0–10 scale (half-star granularity: 9 → 4.5 stars),
   *  or null when the book is unrated. Divide by 2 for a 0–5 star display. */
  rating: number | null;
  cover_url: string | null;
  pubdate: string | null;
  date_added: string | null;
  last_modified: string | null;
  description_html: string | null;
  /** Browser/watch-folder name captured before Calibre renames the import. */
  original_filename: string | null;
  tags: EntityRef[];
  languages: EntityRef[];
  publishers: EntityRef[];
  identifiers: { type: string; val: string; url: string | null; label: string }[];
  /** Displayable Calibre custom metadata; optional for rolling upgrades. */
  custom_columns?: CustomColumn[];
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
  /** When progress was last synced, as an ISO Date, or null when not synced. */
  kosync_progress_timestamp: string | null;
  /** When progress was first synced (the "started reading" date), as a
   *  ISO date, or null when not synced or for progress that predates
   *  this field. */
  kosync_progress_created_at: string | null;
  /** Allowed conversion source/target formats for this book, derived from the
   *  configured converters and formats already present (mirror of the legacy
   *  edit page). Absent on older servers → no conversion UI. */
  convert_options?: {
    sources: string[];
    targets: string[];
  };
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
  mail_body_text: string | null;
  kobo_only_shelves_sync: boolean;
  opds_only_shelves_sync: boolean;
  locale: string;
  default_language: string;
  theme: string;
  ui_font_body: string;
  ui_font_display: string;
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
  mail_body_text?: string;
  kobo_only_shelves_sync?: boolean;
  opds_only_shelves_sync?: boolean;
  locale?: string;
  default_language?: string;
  theme?: string;
  ui_font_body?: string;
  ui_font_display?: string;
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
  /** Publication date as YYYY-MM-DD, or "" when the book has no pubdate
   *  (calibre's year-101 DEFAULT_PUBDATE sentinel). Editable in the SPA (#689). */
  pubdate: string;
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

/** Stable signal that a protected request has already started the canonical
 * top-level authentication transition. Callers must not inspect its body. */
export class AuthTransitionError extends Error {
  constructor() {
    super('Authentication transition in progress');
    this.name = 'AuthTransitionError';
  }
}

let _logoutNavigationStarted = false;

/** Leave the authenticated SPA through the app-owned, prefix-aware route.
 * Never derive this destination from a failed response (open-redirect guard). */
export function navigateToLogout(): void {
  if (_logoutNavigationStarted || typeof window === 'undefined') return;
  _logoutNavigationStarted = true;
  window.location.assign(apiUrl('/logout'));
}

export interface ApiRequestOptions {
  auth?: 'protected' | 'public';
}

function isProtected(options?: ApiRequestOptions): boolean {
  return options?.auth !== 'public';
}

let _sessionProbeInFlight: Promise<boolean> | null = null;

/** Ask the one endpoint that can answer "is this browser still signed in?"
 * authoritatively, and treat anything less than a positive answer as "still
 * signed in".
 *
 * Every other signal we have is circumstantial: a 401 can come from a route's
 * own permission check, and a redirect-to-HTML can come from an intermediary
 * having a bad minute. Acting on circumstantial evidence is expensive here,
 * because the remedy (navigating to /logout) DESTROYS the session server-side —
 * cps/logout.py::cleanup_local_logout deletes the User_Sessions row and clears
 * the remember-me cookie. A false positive therefore does not merely show a
 * login screen, it signs the user out for real and no retry can recover it.
 *
 * Single-flight: a server that just dropped one request is usually dropping the
 * several others the page had in flight, and answering each of them with its own
 * probe would aim a burst at the exact server that is already struggling. Only
 * the in-flight promise is shared, never a settled result — the session's state
 * is not ours to cache. */
function sessionIsGone(): Promise<boolean> {
  if (!_sessionProbeInFlight) {
    _sessionProbeInFlight = probeSession().finally(() => {
      _sessionProbeInFlight = null;
    });
  }
  return _sessionProbeInFlight;
}

/** `redirect: 'manual'` is load-bearing. An expired external-auth session (#824,
 * Authelia) answers with a cross-origin redirect to the IdP; following it would
 * trip CORS and reject this probe with a TypeError, which is indistinguishable
 * from "the server is unreachable". Not following it turns that same case into
 * an inspectable opaqueredirect, so #824 stays detected while a genuine
 * transport fault stays a transport fault. */
/** Long enough that a merely slow server still gets to answer, short enough that
 * a hung one doesn't hold the caller's promise open. A degraded server is the
 * condition this code runs in, so an unbounded probe would leave the request that
 * triggered it never settling — the UI would spin instead of reporting an error,
 * which is a worse version of the symptom being fixed. */
const SESSION_PROBE_TIMEOUT_MS = 5000;

async function probeSession(): Promise<boolean> {
  let probe: Response;
  // AbortController rather than AbortSignal.timeout(): this ships to whatever
  // browser the reader already has, and Safari only grew the latter in 16.
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), SESSION_PROBE_TIMEOUT_MS);
  try {
    probe = await fetch(apiUrl('/api/v1/auth/me'), {
      credentials: 'include',
      redirect: 'manual',
      signal: abort.signal,
    });
  } catch {
    // The probe could not reach the server, or ran out of time. That is evidence
    // about the network, not about the session — fail safe and keep the session.
    return false;
  } finally {
    // Must clear on the success path too: the signal stays live until it fires,
    // and aborting after the headers arrive would tear down the body read below.
    clearTimeout(timer);
  }
  // An intermediary is intercepting authenticated requests, or the app says
  // outright that nobody is signed in.
  if (probe.type === 'opaqueredirect' || probe.status === 401) return true;
  // Any other status is an intermediary talking, not this endpoint — its own
  // contract is 200 or 401. A proxy answering 403 or 5xx for a moment is exactly
  // the ambiguous evidence this function exists to stop acting on, so it is not
  // treated as proof. The user is not stranded by that: a reload re-runs the
  // public useMe(), which renders the login tree when the session really is gone.
  if (!probe.ok) return false;
  try {
    // With anonymous browsing on, a lost session doesn't 401 — /me answers for
    // the Guest row instead (#1023), so `role.anonymous` is the discriminator.
    const me = await probe.json() as { role?: { anonymous?: boolean } };
    return !!me.role?.anonymous;
  } catch {
    return false;
  }
}

async function classifiedFetch(
  path: string,
  init: RequestInit,
  options?: ApiRequestOptions,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (error) {
    // A rejected fetch is a TRANSPORT failure — connection reset, read timeout,
    // DNS, offline, or a CORS-blocked redirect. None of those are proof that the
    // session ended, so confirm before spending the destructive remedy. This is
    // #1067: on a NAS that occasionally drops a request, treating every dropped
    // request as an expired session signed people out mid-browse, and because
    // /logout deletes the session server-side, "remember me" could not bring
    // them back.
    if (isProtected(options) && error instanceof TypeError && await sessionIsGone()) {
      navigateToLogout();
      throw new AuthTransitionError();
    }
    throw error;
  }

  // Do not inspect Location or response.url. The only navigation target is the
  // app-owned /logout route above, regardless of what an intermediary returned.
  const redirectedToHtml = response.redirected
    && (response.headers.get('content-type') || '').includes('text/html');
  if (isProtected(options)
      && (response.type === 'opaqueredirect'
        || response.status === 302
        || response.status === 401
        || redirectedToHtml)
      && await sessionIsGone()) {
    navigateToLogout();
    throw new AuthTransitionError();
  }
  return response;
}

let _csrfCache: string | null = null;

export async function getCsrf(options?: ApiRequestOptions): Promise<string> {
  if (_csrfCache) return _csrfCache;
  const res = await classifiedFetch('/api/v1/auth/csrf', { credentials: 'include' }, options);
  if (!res.ok) throw new ApiError(res.status, 'Failed to fetch CSRF token');
  const data = await res.json() as { csrf_token: string };
  _csrfCache = data.csrf_token;
  return _csrfCache;
}

function clearCsrf() {
  _csrfCache = null;
}

export async function apiGet<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const res = await classifiedFetch(path, { credentials: 'include' }, options);
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

export async function apiPost<T>(
  path: string,
  body?: unknown,
  requestOptions?: Pick<RequestInit, 'keepalive'> & ApiRequestOptions,
): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> => {
    const { auth: _auth, ...fetchOptions } = requestOptions ?? {};
    return classifiedFetch(path, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      ...fetchOptions,
    }, requestOptions);
  };

  let csrf = await getCsrf(requestOptions);
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
    csrf = await getCsrf(requestOptions);
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
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  return JSON.parse(text) as T;
}

/** DELETE with the same CSRF/base-path handling as apiPost (#782 — the reader
 *  needs to remove a highlight). DELETE responses are frequently empty or 204,
 *  so this tolerates a missing body rather than throwing on res.json(). */
export async function apiDelete<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const doDelete = async (csrf: string): Promise<Response> =>
    classifiedFetch(path, {
      method: 'DELETE',
      credentials: 'include',
      headers: { 'X-CSRFToken': csrf },
    }, options);

  let csrf = await getCsrf(options);
  let res = await doDelete(csrf);

  // Same stale-CSRF replay as apiPost — a non-JSON 400 is the global HTML error
  // page triggered by a bad token; a JSON 400 is one of our own validation errors.
  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf(options);
    res = await doDelete(csrf);
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

  // 204 No Content, or any empty body → resolve undefined. A JSON body is parsed.
  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch { /* not JSON (e.g. an empty/whitespace body) — tolerate it */ }
  return undefined as unknown as T;
}

/** PATCH with the same CSRF/base-path handling as apiPost (#782 — the reader
 *  recolors an existing highlight). Mirrors apiPost's JSON body + replay. */
export async function apiPatch<T>(path: string, body?: unknown, options?: ApiRequestOptions): Promise<T> {
  const doPatch = async (csrf: string): Promise<Response> =>
    classifiedFetch(path, {
      method: 'PATCH',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }, options);

  let csrf = await getCsrf(options);
  let res = await doPatch(csrf);

  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf(options);
    res = await doPatch(csrf);
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

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

/** Form-encoded POST (application/x-www-form-urlencoded). Used to consume the
 *  legacy form endpoints (e.g. /metadata/search) directly, reusing their logic
 *  rather than duplicating it under /api/v1. Same CSRF-retry as apiPost. */
export async function apiPostForm<T>(path: string, fields: Record<string, string>, options?: ApiRequestOptions): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> =>
    classifiedFetch(path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrf },
      body: new URLSearchParams(fields).toString(),
    }, options);

  let csrf = await getCsrf(options);
  let res = await doPost(csrf);
  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf(options);
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

export interface MetadataProvider {
  name: string;
  active: boolean;
  initial: boolean;
  id: string;
  globally_enabled: boolean;
}

/** Read the per-user metadata-provider settings shared with the classic UI. */
export function getMetadataProviders(): Promise<MetadataProvider[]> {
  return apiGet<MetadataProvider[]>('/metadata/provider');
}

/** Persist one provider toggle to current_user.view_settings["metadata"]. */
export function setMetadataProviderActive(id: string, value: boolean): Promise<void> {
  return apiPost<void>(`/metadata/provider/${encodeURIComponent(id)}`, { id, value });
}

/** Multipart POST (file upload). Mirrors apiPost's CSRF handling, but lets the
 *  browser set the multipart Content-Type + boundary (so we must NOT set it). */
export async function apiUpload<T>(path: string, formData: FormData, options?: ApiRequestOptions): Promise<T> {
  const doPost = async (csrf: string): Promise<Response> =>
    classifiedFetch(path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRFToken': csrf },
      body: formData,
    }, options);

  let csrf = await getCsrf(options);
  let res = await doPost(csrf);

  const isJson400 = res.status === 400
    && (res.headers.get('content-type') || '').includes('application/json');
  if (res.status === 400 && !isJson400) {
    clearCsrf();
    csrf = await getCsrf(options);
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
