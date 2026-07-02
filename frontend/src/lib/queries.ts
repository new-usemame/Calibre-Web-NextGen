import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPost, apiUpload, apiPostForm, ApiError } from './api';
import { removeBookFromCache } from './scrollCache';
import type { MetaSearchResponse } from './api';
import type {
  Me, BooksPage, BookDetail, EntityList, Shelf, ShelfDetail,
  SearchOptions, AdvancedSearchParams, AdvSearchResult, Account, ProfileUpdate,
  BookMetadata, MetadataUpdate, UploadResult, AdminUser, AboutInfo, TaskItem, AuthConfig,
} from './api';

/** Entity kinds the catalog can be filtered by. Singular here; the browse-list
 *  endpoints/routes use the plural (author -> authors). */
export type EntityKind = 'author' | 'series' | 'tag' | 'publisher' | 'language' | 'rating' | 'format';
export type ReadFilter = 'all' | 'read' | 'unread';
/** Discovery "views" — server-side ?filter= categories beyond read/unread. */
export type DiscoveryView = 'hot' | 'discover' | 'rated' | 'favorites' | 'archived';

/** Map a singular entity kind to its plural browse endpoint/route segment. */
export const ENTITY_PLURAL: Record<EntityKind, string> = {
  author: 'authors',
  series: 'series',
  tag: 'tags',
  publisher: 'publishers',
  language: 'languages',
  rating: 'ratings',
  format: 'formats',
};

export interface BooksQuery {
  page: number;
  search?: string;
  sort?: string;
  readFilter?: ReadFilter;
  entityKind?: EntityKind;
  entityId?: string | number;
  /** Discovery view (hot/discover/rated/favorites/archived) — sent as ?filter=. */
  view?: DiscoveryView;
}

export function useMe() {
  return useQuery<Me | null>({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await apiGet<Me>('/api/v1/auth/me');
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
    staleTime: 60000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { username: string; password: string; remember?: boolean }) =>
      apiPost<Me>('/api/v1/auth/login', vars),
    onSuccess: (data) => {
      queryClient.setQueryData(['me'], data);
      void queryClient.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export interface MagicLinkSession {
  token: string;
  verify_url: string;
  qrcode: string;
  expires_in_minutes: number;
}

export type MagicLinkPoll =
  | { status: 'not_verified' }
  | { status: 'expired' }
  | { status: 'not_found' }
  | { status: 'success'; user: Me };

/** Start a magic-link (remote) login session: mint a token + QR for this device. */
export function useMagicLinkStart() {
  return useMutation({
    mutationFn: () => apiPost<MagicLinkSession>('/api/v1/auth/magic-link/start'),
  });
}

/** Poll a magic-link token until another signed-in device authorises it. On
 *  success the session cookie is set server-side; we seed the me-cache so the
 *  app flips to the authenticated tree. */
export function useMagicLinkPoll() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) =>
      apiPost<MagicLinkPoll>('/api/v1/auth/magic-link/poll', { token }),
    onSuccess: (data) => {
      if (data.status === 'success') {
        queryClient.setQueryData(['me'], data.user);
        void queryClient.invalidateQueries({ queryKey: ['me'] });
      }
    },
  });
}

/** A short strip of random books for the library "Discover" section. `nonce`
 *  lets the caller reshuffle (bump it to refetch a fresh random set). Reuses the
 *  same server-side discover filter as the full /discover view. */
export function useDiscover(count: number, nonce: number) {
  return useQuery<BooksPage>({
    queryKey: ['discover-strip', count, nonce],
    queryFn: () => apiGet<BooksPage>(`/api/v1/books?filter=discover&per_page=${count}`),
    staleTime: 0,
  });
}

export function useAuthConfig() {
  return useQuery<AuthConfig>({
    queryKey: ['auth-config'],
    queryFn: () => apiGet<AuthConfig>('/api/v1/auth/config'),
    staleTime: Infinity,
  });
}

export function useRegister() {
  return useMutation({
    mutationFn: (vars: { name: string; email: string }) =>
      apiPost<{ ok: boolean; message: string }>('/api/v1/auth/register', vars),
  });
}

export function useForgotPassword() {
  return useMutation({
    mutationFn: (username: string) =>
      apiPost<{ ok: boolean; message: string }>('/api/v1/auth/forgot', { username }),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost('/api/v1/auth/logout'),
    onSuccess: () => {
      queryClient.setQueryData(['me'], null);
      void queryClient.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export function useBooks(q: BooksQuery) {
  const { page, search = '', sort = 'new', readFilter = 'all', entityKind, entityId, view } = q;
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('per_page', '24');
  params.set('sort', sort);
  // The API's search path is separate from entity/read filtering, so search is
  // only sent in the unfiltered library view (the UI hides the search box when
  // an entity filter is active).
  if (search && !entityKind && !view) params.set('search', search);
  // A discovery view (hot/discover/rated/favorites/archived) owns ?filter=;
  // otherwise the read/unread segmented control does.
  if (view) params.set('filter', view);
  else if (readFilter !== 'all') params.set('filter', readFilter);
  if (entityKind && entityId !== undefined && entityId !== '') {
    params.set(entityKind, String(entityId));
  }
  return useQuery<BooksPage>({
    queryKey: ['books', page, search, sort, readFilter, entityKind ?? '', entityId ?? '', view ?? ''],
    queryFn: () => apiGet<BooksPage>(`/api/v1/books?${params.toString()}`),
    placeholderData: (prev) => prev,
  });
}

/** Fetch an entity-browse list (authors/series/tags/publishers/languages).
 *  `plural` is the endpoint segment (e.g. "authors"). */
export function useEntityList(plural: string) {
  return useQuery<EntityList>({
    queryKey: ['entities', plural],
    queryFn: () => apiGet<EntityList>(`/api/v1/${plural}`),
    staleTime: 60000,
  });
}

export function useBook(id: string | number) {
  return useQuery<BookDetail>({
    queryKey: ['book', String(id)],
    queryFn: () => apiGet<BookDetail>(`/api/v1/books/${id}`),
  });
}

export function useToggleRead(id: string | number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (read: boolean) =>
      apiPost<{ read: boolean }>(`/api/v1/books/${id}/read`, { read }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['book', String(id)] });
      void queryClient.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

/** Star/unstar a book for the current user. Server is presence-based; we just
 *  refetch the detail so the star reflects the new state. */
export function useToggleFavorite(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<{ favorited: boolean }>(`/api/v1/books/${id}/favorite`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['book', String(id)] }),
  });
}

/** Archive/unarchive (sync-pause). */
export function useToggleArchived(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<{ archived: boolean }>(`/api/v1/books/${id}/archived`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

/** Hide/unhide for the current user (hide gated server-side on the admin flag). */
export function useToggleHidden(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<{ hidden: boolean }>(`/api/v1/books/${id}/hidden`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

/** Email a book to the user's e-reader (optionally converting / to other addresses). */
export function useSendToEreader(id: string | number) {
  return useMutation({
    mutationFn: (v: { format: string; convert?: boolean; emails?: string }) =>
      apiPost<{ ok: boolean; message: string }>(`/api/v1/books/${id}/send`, v),
  });
}

// ── Shelves ──────────────────────────────────────────────────────────────────

export function useShelves() {
  return useQuery<{ items: Shelf[] }>({
    queryKey: ['shelves'],
    queryFn: () => apiGet<{ items: Shelf[] }>('/api/v1/shelves'),
    staleTime: 30000,
  });
}

export function useShelf(id: string | number | undefined, page = 1) {
  return useQuery<ShelfDetail>({
    queryKey: ['shelf', String(id), page],
    queryFn: () => apiGet<ShelfDetail>(`/api/v1/shelves/${id}?page=${page}&per_page=24`),
    enabled: id !== undefined && id !== '',
    // Keep the previous page's rows only while paging within the SAME shelf —
    // never carry one shelf's rows across an id change, where they'd render
    // under the next shelf's key and mix both shelves' books (#612).
    placeholderData: (prev, prevQuery) =>
      prevQuery && String(prevQuery.queryKey[1]) === String(id) ? prev : undefined,
  });
}

/** Shelf ids (among the user's visible shelves) that currently contain a book. */
export function useBookShelves(bookId: string | number) {
  return useQuery<{ shelf_ids: number[] }>({
    queryKey: ['book-shelves', String(bookId)],
    queryFn: () => apiGet<{ shelf_ids: number[] }>(`/api/v1/books/${bookId}/shelves`),
  });
}

export function useCreateShelf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { name: string; is_public?: boolean }) =>
      apiPost<Shelf>('/api/v1/shelves', vars),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['shelves'] }),
  });
}

export function useUpdateShelf(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { name?: string; is_public?: boolean; kobo_sync?: boolean }) =>
      apiPost<Shelf>(`/api/v1/shelves/${id}`, vars),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['shelves'] });
      void qc.invalidateQueries({ queryKey: ['shelf', String(id)] });
    },
  });
}

export function useDeleteShelf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/api/v1/shelves/${id}/delete`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['shelves'] }),
  });
}

/** Persist a new book order for a shelf (full ordered id list). */
export function useReorderShelfBooks(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (order: number[]) => apiPost<{ ok: boolean }>(`/api/v1/shelves/${id}/order`, { order }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['shelf', String(id)] }),
  });
}

/** Add every book of a series to a shelf (series_index order). */
export function useAddSeriesToShelf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { shelfId: number; seriesId: number }) =>
      apiPost<{ added: number }>(`/api/v1/shelves/${v.shelfId}/series/${v.seriesId}`),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: ['shelf', String(v.shelfId)] });
      void qc.invalidateQueries({ queryKey: ['shelves'] });
    },
  });
}

// ── Admin (user management) ──────────────────────────────────────────────────

export function useAdminUsers() {
  return useQuery<{ items: AdminUser[] }>({
    queryKey: ['admin-users'],
    queryFn: () => apiGet<{ items: AdminUser[] }>('/api/v1/admin/users'),
  });
}

export interface NewUser {
  name: string;
  password: string;
  email?: string;
  kindle_mail?: string;
  roles?: Record<string, boolean>;
  locale?: string;
  default_language?: string;
}

export function useCreateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: NewUser) => apiPost<AdminUser>('/api/v1/admin/users', v),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
}

export interface AdminConfig {
  config_calibre_web_title: string;
  config_books_per_page: number;
  config_random_books: number;
  config_authors_max: number;
  config_theme: number;
  config_default_language: string;
  config_default_locale: string;
  config_server_announcement: string;
  locales: { id: string; name: string }[];
  languages: { id: string; name: string }[];
}

export function useAdminConfig() {
  return useQuery<AdminConfig>({
    queryKey: ['admin-config'],
    queryFn: () => apiGet<AdminConfig>('/api/v1/admin/config'),
  });
}

export function useUpdateAdminConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: Partial<AdminConfig>) => apiPost<AdminConfig>('/api/v1/admin/config', vars),
    onSuccess: (data) => {
      qc.setQueryData(['admin-config'], data);
      void qc.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export interface MailConfig {
  mail_server: string;
  mail_port: number;
  mail_use_ssl: number;
  mail_login: string;
  mail_from: string;
  mail_size_mb: number;
  mail_server_type: number;
  has_password: boolean;
}

export function useMailConfig() {
  return useQuery<MailConfig>({
    queryKey: ['admin-mail'],
    queryFn: () => apiGet<MailConfig>('/api/v1/admin/mailsettings'),
  });
}

export function useUpdateMailConfig() {
  const qc = useQueryClient();
  return useMutation({
    // mail_password is write-only; omit it to keep the existing one.
    mutationFn: (vars: Partial<MailConfig> & { mail_password?: string }) =>
      apiPost<MailConfig>('/api/v1/admin/mailsettings', vars),
    onSuccess: (data) => {
      qc.setQueryData(['admin-mail'], data);
      void qc.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

// --- Deep auth/security config (login type / LDAP / OAuth / SSL / reverse-proxy)
// Secrets are write-only: GET returns has_password / has_secret booleans only.
export interface IdName { id: number; name: string }
export interface SecurityLdap {
  provider_url: string; port: number; encryption: number; authentication: number;
  serv_username: string; has_password: boolean; auto_create_users: boolean;
  dn: string; user_object: string; member_user_object: string;
  group_object_filter: string; group_members_field: string; group_name: string;
  openldap: boolean; cacert_path: string; cert_path: string; key_path: string;
}
export interface SecurityOauthGeneric {
  client_id: string; has_secret: boolean; base_url: string; authorize_url: string;
  token_url: string; userinfo_url: string; admin_group: string; metadata_url: string;
  scope: string; username_mapper: string; email_mapper: string; login_button: string;
  active: boolean;
  // Group-based access control (#494/#495).
  group_claim: string; require_group: boolean; allowed_groups: string;
  default_roles: Record<string, boolean>;
}
export interface SecurityConfig {
  login_type: number;
  login_types: IdName[];
  ldap_auth_levels: IdName[];
  ldap_encryption_levels: IdName[];
  ldap: SecurityLdap;
  oauth: {
    redirect_host: string; disable_standard_login: boolean;
    enable_group_admin_management: boolean; generic: SecurityOauthGeneric;
    providers: { name: string; client_id: string; has_secret: boolean; active: boolean }[];
  };
  ssl: { use_https: boolean; certfile: string; keyfile: string };
  remote_login: boolean;
  reverse_proxy: { enabled: boolean; header_name: string; auto_create_users: boolean };
  reboot_required?: boolean;
}
// The POST shape mirrors the GET shape but secrets are plain (write-only) fields.
export interface SecurityUpdate {
  login_type?: number;
  remote_login?: boolean;
  ldap?: Partial<Omit<SecurityLdap, 'has_password'>> & { serv_password?: string };
  oauth?: {
    redirect_host?: string; disable_standard_login?: boolean; enable_group_admin_management?: boolean;
    generic?: Partial<Omit<SecurityOauthGeneric, 'has_secret' | 'active'>> & { client_secret?: string };
    providers?: { name: string; client_id?: string; client_secret?: string }[];
  };
  ssl?: { use_https?: boolean; certfile?: string; keyfile?: string };
  reverse_proxy?: { enabled?: boolean; header_name?: string; auto_create_users?: boolean };
}

export function useSecurityConfig() {
  return useQuery<SecurityConfig>({
    queryKey: ['admin-security'],
    queryFn: () => apiGet<SecurityConfig>('/api/v1/admin/security'),
  });
}

export function useUpdateSecurityConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: SecurityUpdate) => apiPost<SecurityConfig>('/api/v1/admin/security', vars),
    onSuccess: (data) => qc.setQueryData(['admin-security'], data),
  });
}

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: number; roles?: Record<string, boolean>; email?: string }) => {
      const { id, ...body } = v;
      return apiPost<AdminUser>(`/api/v1/admin/users/${id}`, body);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
}

export function useDeleteAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/api/v1/admin/users/${id}/delete`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
}

// ── Bulk operations ──────────────────────────────────────────────────────────

/** Bulk actions over a set of book ids, each implemented as a fan-out over the
 *  existing per-book endpoints (settle-all so one failure doesn't abort the
 *  batch). Suitable for the moderate selections the catalog allows. */
export function useBulkActions() {
  const qc = useQueryClient();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['books'] });
    void qc.invalidateQueries({ queryKey: ['shelves'] });
  };
  const settle = (ps: Promise<unknown>[]) => Promise.allSettled(ps);

  const markRead = useMutation({
    mutationFn: (v: { ids: number[]; read: boolean }) =>
      settle(v.ids.map((id) => apiPost(`/api/v1/books/${id}/read`, { read: v.read }))),
    onSuccess: refresh,
  });
  const addToShelf = useMutation({
    mutationFn: (v: { ids: number[]; shelfId: number }) =>
      // tolerate 409 (already on shelf) per book
      settle(v.ids.map((id) => apiPost(`/api/v1/shelves/${v.shelfId}/books/${id}`).catch(() => null))),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => settle(ids.map((id) => apiPost(`/api/v1/books/${id}/delete`))),
    onSuccess: (_data, ids) => {
      // Evict deleted books from every cached catalog snapshot so a later
      // scroll-restore can't resurrect them as ghost cards (#578).
      ids.forEach(removeBookFromCache);
      refresh();
    },
  });
  // Bulk metadata: apply the same partial field set to every selected book via
  // the per-book metadata endpoint (replace semantics for the filled fields).
  const setMetadata = useMutation({
    mutationFn: (v: { ids: number[]; fields: MetadataUpdate }) =>
      settle(v.ids.map((id) => apiPost(`/api/v1/books/${id}/metadata`, v.fields))),
    onSuccess: refresh,
  });
  return { markRead, addToShelf, remove, setMetadata };
}

/** Merge books: the first id is the target (kept); the rest are merged into it
 *  (their formats copied over, then deleted). Reuses the legacy /ajax/mergebooks. */
export function useMergeBooks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) => apiPost('/ajax/mergebooks', { Merge_books: ids }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['books'] }),
  });
}

// ── Upload ───────────────────────────────────────────────────────────────────

export function useUploadBooks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      for (const f of files) fd.append('file', f);
      return apiUpload<UploadResult>('/api/v1/upload', fd);
    },
    onSuccess: () => {
      // The library will populate as ingest processes; nudge the catalog.
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

// ── Edit metadata ────────────────────────────────────────────────────────────

export function useBookMetadata(id: string | number) {
  return useQuery<BookMetadata>({
    queryKey: ['metadata', String(id)],
    queryFn: () => apiGet<BookMetadata>(`/api/v1/books/${id}/metadata`),
  });
}

export function useUpdateMetadata(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: MetadataUpdate) => apiPost<BookMetadata>(`/api/v1/books/${id}/metadata`, vars),
    onSuccess: (data) => {
      qc.setQueryData(['metadata', String(id)], data);
      // The detail/catalog views show the same fields — refresh them.
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

/** Delete a single format from a book (keeps the book). */
export function useDeleteFormat(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fmt: string) =>
      apiPost(`/api/v1/books/${id}/formats/${encodeURIComponent(fmt)}/delete`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

/** Add a format (file) to an existing book via the ingest pipeline. */
export function useAddFormat(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      return apiUpload<{ queued: string }>(`/api/v1/books/${id}/formats`, fd);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['book', String(id)] }),
  });
}

/** Queue a format conversion (from -> to). */
export function useConvertFormat(id: string | number) {
  return useMutation({
    mutationFn: (v: { from: string; to: string }) =>
      apiPost<{ ok: boolean; message: string }>(`/api/v1/books/${id}/convert`, v),
  });
}

/** Search online metadata providers (reuses the legacy /metadata/search). */
export function useMetadataSearch() {
  return useMutation({
    mutationFn: (query: string) => apiPostForm<MetaSearchResponse>('/metadata/search', { query }),
  });
}

/** Replace the cover from an uploaded file or a remote URL. */
export function useSetCover(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { file?: File; url?: string }) => {
      if (v.file) {
        const fd = new FormData();
        fd.append('file', v.file);
        return apiUpload<{ ok: boolean; cover_url: string }>(`/api/v1/books/${id}/cover`, fd);
      }
      return apiPost<{ ok: boolean; cover_url: string }>(`/api/v1/books/${id}/cover`, { url: v.url });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
      void qc.invalidateQueries({ queryKey: ['books'] });
    },
  });
}

// ── Reader (bookmark / progress) ─────────────────────────────────────────────

export function useBookmark(bookId: string | number, format = 'epub') {
  return useQuery<{ bookmark: string | null }>({
    queryKey: ['bookmark', String(bookId), format],
    queryFn: () => apiGet<{ bookmark: string | null }>(
      `/api/v1/books/${bookId}/bookmark?format=${encodeURIComponent(format)}`),
    staleTime: 0,
  });
}

export function useSaveBookmark(bookId: string | number) {
  return useMutation({
    mutationFn: (vars: { format: string; bookmark: string }) =>
      apiPost(`/api/v1/books/${bookId}/bookmark`, vars),
  });
}

// ── Account ──────────────────────────────────────────────────────────────────

export function useAccount() {
  return useQuery<Account>({
    queryKey: ['account'],
    queryFn: () => apiGet<Account>('/api/v1/account'),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: ProfileUpdate) => apiPost<Account>('/api/v1/account/profile', vars),
    onSuccess: (data) => {
      qc.setQueryData(['account'], data);
      // name/locale also surface in the top bar via useMe
      void qc.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (vars: { current_password: string; new_password: string }) =>
      apiPost('/api/v1/account/password', vars),
  });
}

/** Create an app password (for OPDS/KOSync). Returns the cleartext token once. */
export function useCreateAppPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (label: string) =>
      apiPost<{ id: number; label: string; token: string }>('/api/v1/account/app-passwords', { label }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['account'] }),
  });
}

export function useRevokeAppPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/api/v1/account/app-passwords/${id}/delete`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['account'] }),
  });
}

// ── Advanced search ──────────────────────────────────────────────────────────

export function useSearchOptions() {
  return useQuery<SearchOptions>({
    queryKey: ['search-options'],
    queryFn: () => apiGet<SearchOptions>('/api/v1/search/options'),
    staleTime: 60000,
  });
}

/** Run advanced search. `params` is null until the user submits, which keeps the
 *  query disabled (and the results pane empty) on first load. */
export function useAdvancedSearch(params: AdvancedSearchParams | null, page: number) {
  return useQuery<AdvSearchResult>({
    queryKey: ['adv-search', params, page],
    queryFn: () => apiPost<AdvSearchResult>('/api/v1/search/advanced', { ...params, page, per_page: 24 }),
    enabled: params !== null,
    placeholderData: (prev) => prev,
  });
}

/** Add or remove a book from a shelf; invalidates the affected caches. */
export function useShelfMembership() {
  const qc = useQueryClient();
  const invalidate = (shelfId: number, bookId: number) => {
    void qc.invalidateQueries({ queryKey: ['shelf', String(shelfId)] });
    void qc.invalidateQueries({ queryKey: ['shelves'] });
    void qc.invalidateQueries({ queryKey: ['book-shelves', String(bookId)] });
  };
  const add = useMutation({
    mutationFn: (v: { shelfId: number; bookId: number }) =>
      apiPost(`/api/v1/shelves/${v.shelfId}/books/${v.bookId}`),
    onSuccess: (_d, v) => invalidate(v.shelfId, v.bookId),
  });
  const remove = useMutation({
    mutationFn: (v: { shelfId: number; bookId: number }) =>
      apiPost(`/api/v1/shelves/${v.shelfId}/books/${v.bookId}/delete`),
    onSuccess: (_d, v) => invalidate(v.shelfId, v.bookId),
  });
  return { add, remove };
}

// ── Magic shelves (smart collections) ────────────────────────────────────────

export interface MagicRule { id: string; operator: string; value: string }
export interface MagicRuleSet { condition: 'AND' | 'OR'; rules: MagicRule[] }

export function useMagicShelfPreview() {
  return useMutation({
    mutationFn: (rules: MagicRuleSet) =>
      apiPost<{ success: boolean; count: number; sample_books: string[]; message?: string }>(
        '/magicshelf/preview', { rules }),
  });
}

export function useCreateMagicShelf() {
  return useMutation({
    mutationFn: (v: { name: string; icon: string; rules: MagicRuleSet }) =>
      apiPost<{ success: boolean; shelf_id?: number; message?: string }>('/magicshelf', v),
  });
}

export function useEditMagicShelf(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { name: string; icon: string; rules: MagicRuleSet }) =>
      apiPost<{ success: boolean; message?: string }>(`/magicshelf/${id}/edit`, v),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['magicshelves'] });
      void qc.invalidateQueries({ queryKey: ['magicshelf', String(id)] });
    },
  });
}

export interface MagicShelfItem { id: number; name: string; icon: string; is_public: boolean; is_owner: boolean }

export function useMagicShelves() {
  return useQuery<{ items: MagicShelfItem[] }>({
    queryKey: ['magicshelves'],
    queryFn: () => apiGet<{ items: MagicShelfItem[] }>('/api/v1/magicshelves'),
    staleTime: 30000,
  });
}

export function useMagicShelfBooks(id: string | number, page = 1) {
  return useQuery<{ id: number; name: string; icon: string; is_owner: boolean } & BooksPage>({
    queryKey: ['magicshelf', String(id), page],
    queryFn: () => apiGet(`/api/v1/magicshelf/${id}?page=${page}`),
    // Same-shelf paging only — see useShelf (#612).
    placeholderData: (prev, prevQuery) =>
      prevQuery && String(prevQuery.queryKey[1]) === String(id) ? prev : undefined,
  });
}

export function useDeleteMagicShelf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/magicshelf/${id}/delete`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['magicshelves'] }),
  });
}

export function useDuplicateMagicShelf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/magicshelf/${id}/duplicate`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['magicshelves'] }),
  });
}

// ── Duplicates ───────────────────────────────────────────────────────────────

export interface DuplicateBook {
  id: number;
  title: string;
  authors: string;
  formats: string[];
  cover_url: string | null;
}
export interface DuplicateGroup {
  group_hash: string;
  title: string;
  author: string;
  count: number;
  books: DuplicateBook[];
}

export function useDuplicates() {
  return useQuery<{ items: DuplicateGroup[]; needs_scan: boolean }>({
    queryKey: ['duplicates'],
    queryFn: () => apiGet<{ items: DuplicateGroup[]; needs_scan: boolean }>('/api/v1/duplicates'),
  });
}

/** Dismiss a duplicate group — reuses the legacy JSON route. */
export function useDismissDuplicate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupHash: string) =>
      apiPost(`/duplicates/dismiss/${encodeURIComponent(groupHash)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['duplicates'] }),
  });
}

// ── Info: About / Tasks ──────────────────────────────────────────────────────

export function useAbout() {
  return useQuery<AboutInfo>({
    queryKey: ['about'],
    queryFn: () => apiGet<AboutInfo>('/api/v1/about'),
    staleTime: 60000,
  });
}

export function useTasks() {
  return useQuery<{ items: TaskItem[] }>({
    queryKey: ['tasks'],
    queryFn: () => apiGet<{ items: TaskItem[] }>('/api/v1/tasks'),
    refetchInterval: 4000, // live queue
  });
}

export function useCancelTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: number | string) =>
      apiPost(`/api/v1/tasks/${encodeURIComponent(String(taskId))}/cancel`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['tasks'] }),
  });
}
