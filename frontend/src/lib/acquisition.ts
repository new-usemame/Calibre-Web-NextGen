/**
 * Client contract for the gated OPDS acquisition API (`cps/api/acquisition.py`).
 *
 * Two rules this module exists to keep:
 *  1. The browser never sees a source URL or a credential. Every navigation step
 *     is an opaque server-issued `selection`, and every downloadable file is an
 *     opaque server-issued `offer_id`. Nothing here builds an upstream URL.
 *  2. "Downloaded" is not "imported". A job is only finished when the server
 *     returns a `result` (the import receipt) carrying real Calibre book IDs.
 */
import { apiGet, apiPatch, apiPost } from './api';

/** Terminal success is `imported` — the receipt exists. `staged`/`publishing`
 *  mean bytes are on disk but no book exists yet, which is exactly the
 *  distinction the status line has to keep visible.
 *
 *  This is the state set the server can actually persist: the writes in
 *  `services/acquisition/storage.py` and `worker.py`, plus `WORK_STATES`.
 *  Note `source_busy` is deliberately NOT here — it is an `error_code` carried
 *  by a `failed` job, never a state. */
export type AcquisitionJobState =
  | 'awaiting_approval'
  | 'queued'
  | 'resolving'
  | 'downloading'
  | 'staged'
  | 'publishing'
  | 'importing'
  | 'imported'
  | 'failed'
  | 'cancelled';

/** The states on which the server owes nothing further. Everything is defined
 *  against THIS set rather than against a list of busy states, because the bug
 *  that shape invites is forgetting one: `awaiting_approval` was missing from
 *  the old busy list, so a user whose only request was pending approval stopped
 *  polling and never saw it approved or imported without reloading. Deriving
 *  "still pending" by negation means an unfamiliar state — including one a
 *  newer server invented — keeps the page watching instead of going quiet. */
export const ACQUISITION_TERMINAL_STATES: ReadonlySet<string> = new Set<AcquisitionJobState>([
  'imported', 'failed', 'cancelled',
]);

/** True while the job may still change by itself or by an administrator. */
export function isAcquisitionPending(state: string): boolean {
  return !ACQUISITION_TERMINAL_STATES.has(state);
}

/** Exactly the states `AcquisitionRepository.cancel` accepts. Offering Cancel
 *  outside this set produces a 409 the user did not ask for: once a job reaches
 *  `publishing`/`importing` the bytes are already being handed to the library
 *  and the server refuses to stop it. */
export const ACQUISITION_CANCELLABLE_STATES: ReadonlySet<string> = new Set<AcquisitionJobState>([
  'awaiting_approval', 'queued', 'resolving', 'downloading', 'staged',
]);

export interface AcquisitionRuntime {
  available: boolean;
  /** Machine-readable preconditions that are not met, e.g. `ingest_unwritable`. */
  reasons: string[];
}

export interface AcquisitionConnection {
  id: string;
  label: string;
  adapter: string;
  enabled: boolean;
  revision: number;
}

export interface AcquisitionInstanceState {
  enabled: boolean;
  migration_status: 'ready' | 'needs_review' | 'unavailable';
  runtime: AcquisitionRuntime;
}

export interface AcquisitionOffer {
  format: 'EPUB' | 'PDF';
  label: string | null;
  /** Stable per-file display identity — a React key, never an authorization. */
  identity: string;
  relation: string;
  /** Opaque, owner-bound, expiring. The only thing a request may reference. */
  offer_id: string;
}

export interface AcquisitionNavigation {
  title: string;
  relations: string[];
  /** Opaque server-side cursor; pass back as `?selection=`. */
  selection: string;
}

export interface AcquisitionPublication {
  title: string;
  identity: string;
  authors: string[];
  languages: string[];
  description: string | null;
  offers: AcquisitionOffer[];
  navigation: AcquisitionNavigation[];
}

export interface AcquisitionSearchCapability {
  title: string;
  selection: string;
}

export interface AcquisitionSection {
  title: string;
  publications: AcquisitionPublication[];
  navigation: AcquisitionNavigation[];
}

export interface AcquisitionCatalog {
  title: string;
  protocol: string;
  publications: AcquisitionPublication[];
  navigation: AcquisitionNavigation[];
  pagination: AcquisitionNavigation[];
  searches: AcquisitionSearchCapability[];
  groups: AcquisitionSection[];
  facets: { title: string; navigation: AcquisitionNavigation[] }[];
}

export interface AcquisitionReceipt {
  /** Real Calibre IDs from the import receipt, already filtered to the ones
   *  this account may actually see. */
  book_ids: number[];
  disposition: 'imported' | 'already_imported' | 'existing_retained';
}

export interface AcquisitionJob {
  id: string;
  connection_id: string;
  state: AcquisitionJobState | string;
  add_to_my_library: boolean;
  cancel_requested: boolean;
  error_code: string | null;
  claim_count: number;
  title: string | null;
  result?: AcquisitionReceipt;
}

export interface AcquisitionBootstrap {
  connections: AcquisitionConnection[];
  /** The account holds auto-approve, so a request starts immediately instead of
   *  queuing for an administrator. Drives the Download / Request button label. */
  can_acquire: boolean;
  runtime: AcquisitionRuntime;
}

export interface AcquisitionProbe {
  title: string;
  protocol: string;
  browse: boolean;
  search_advertised: boolean;
  direct_download_advertised: boolean;
}

export interface AcquisitionGrant {
  id: number;
  name: string;
  access: boolean;
  auto_approve: boolean;
}

export interface AcquisitionConnectionInput {
  endpoint: string;
  auth_kind: 'none' | 'basic' | 'bearer';
  username: string;
  secret: string;
}

const BASE = '/api/v1';

/* ---------------------------------------------------------------- user side */

export function getAcquisitionBootstrap(): Promise<AcquisitionBootstrap> {
  return apiGet<AcquisitionBootstrap>(`${BASE}/acquisition`);
}

/** Browse or search one connection. `selection` is the server's opaque cursor
 *  (a navigation link, or the catalog's advertised search capability); `query`
 *  is only legal alongside a search selection. */
export function getAcquisitionCatalog(
  connection: string,
  options?: { selection?: string; query?: string },
): Promise<AcquisitionCatalog> {
  const params = new URLSearchParams({ connection });
  if (options?.selection) params.set('selection', options.selection);
  if (options?.query) params.set('q', options.query);
  return apiGet<AcquisitionCatalog>(`${BASE}/acquisition/catalog?${params.toString()}`);
}

export function getAcquisitionJobs(): Promise<{ jobs: AcquisitionJob[] }> {
  return apiGet<{ jobs: AcquisitionJob[] }>(`${BASE}/acquisition/jobs`);
}

/** `idempotency_key` is unique per (owner, key), so a double-click or a retried
 *  5xx resolves to the same job instead of two downloads. */
export function createAcquisitionJob(body: {
  connection_id: string;
  offer_id: string;
  idempotency_key: string;
  add_to_my_library: boolean;
}): Promise<AcquisitionJob> {
  return apiPost<AcquisitionJob>(`${BASE}/acquisition/jobs`, body);
}

export function cancelAcquisitionJob(id: string): Promise<AcquisitionJob> {
  return apiPost<AcquisitionJob>(`${BASE}/acquisition/jobs/${encodeURIComponent(id)}/cancel`);
}

export function retryAcquisitionJob(id: string): Promise<AcquisitionJob> {
  return apiPost<AcquisitionJob>(`${BASE}/acquisition/jobs/${encodeURIComponent(id)}/retry`);
}

/* --------------------------------------------------------------- admin side */

export function getAcquisitionSettings(): Promise<AcquisitionInstanceState> {
  return apiGet<AcquisitionInstanceState>(`${BASE}/admin/acquisition`);
}

export function setAcquisitionEnabled(enabled: boolean): Promise<AcquisitionInstanceState> {
  return apiPatch<AcquisitionInstanceState>(`${BASE}/admin/acquisition`, { enabled });
}

export function getAcquisitionConnections(): Promise<{ connections: AcquisitionConnection[] }> {
  return apiGet<{ connections: AcquisitionConnection[] }>(`${BASE}/admin/acquisition/connections`);
}

export function createAcquisitionConnection(
  label: string,
  config: AcquisitionConnectionInput,
): Promise<AcquisitionConnection> {
  return apiPost<AcquisitionConnection>(`${BASE}/admin/acquisition/connections`, {
    label, adapter: 'opds', config,
  });
}

export function setAcquisitionConnectionEnabled(id: string, enabled: boolean): Promise<{ ok: true }> {
  return apiPatch<{ ok: true }>(`${BASE}/admin/acquisition/connections/${encodeURIComponent(id)}`, { enabled });
}

/** Reads a bounded catalog document only — it never follows an acquisition link
 *  and never downloads a book. */
export function probeAcquisitionConnection(id: string): Promise<AcquisitionProbe> {
  return apiPost<AcquisitionProbe>(`${BASE}/admin/acquisition/connections/${encodeURIComponent(id)}/probe`);
}

export function getAcquisitionGrants(): Promise<{ users: AcquisitionGrant[] }> {
  return apiGet<{ users: AcquisitionGrant[] }>(`${BASE}/admin/acquisition/users`);
}

export function setAcquisitionGrant(
  ownerId: number,
  grant: { access: boolean; auto_approve: boolean },
): Promise<{ ok: true }> {
  return apiPatch<{ ok: true }>(`${BASE}/admin/acquisition/users/${ownerId}`, grant);
}

export function getAcquisitionApprovalQueue(): Promise<{ jobs: (AcquisitionJob & { owner_id: number })[] }> {
  return apiGet<{ jobs: (AcquisitionJob & { owner_id: number })[] }>(`${BASE}/admin/acquisition/jobs`);
}

export function approveAcquisitionJob(id: string): Promise<AcquisitionJob> {
  return apiPost<AcquisitionJob>(`${BASE}/admin/acquisition/jobs/${encodeURIComponent(id)}/approve`);
}
