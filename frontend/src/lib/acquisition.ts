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
import { apiDelete, apiGet, apiPatch, apiPost } from './api';

/* The job state machine lives in its own import-free module so the node unit
 * lane can exercise it directly; re-exported here so callers have one import. */
import type { AcquisitionJobState } from './acquisitionJobStates';

export {
  ACQUISITION_CANCELLABLE_STATES,
  ACQUISITION_TERMINAL_STATES,
  isAcquisitionPending,
} from './acquisitionJobStates';
export type { AcquisitionJobState } from './acquisitionJobStates';

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
  format: 'EPUB' | 'PDF' | 'MOBI' | 'NZB' | 'Torrent';
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
  unavailable_reason?: string;
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
  /** Present on a bundle's original job and any selected sibling jobs. */
  bundle_parent_id?: string | null;
  /** True only while this job is the original anchor and can accept choices. */
  bundle_selectable?: boolean;
  result?: AcquisitionReceipt;
}

export interface AcquisitionBundleCandidate {
  /** Opaque, owner-bound candidate identity. Never a path or authorization by itself. */
  id: string;
  name: string;
  format: 'EPUB' | 'PDF' | 'MOBI';
  size: number;
  job_id?: string;
  state?: string;
}

export interface AcquisitionBundleChoices {
  generation: string;
  candidates: AcquisitionBundleCandidate[];
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
  /** Let this one catalog resolve to a private (home or LAN) address. The
   *  server expands it to the allowed ranges and scopes it to this catalog's
   *  origin; loopback, link-local and cloud metadata stay denied either way. */
  allow_private_network?: boolean;
  allow_mobi?: boolean;
  download_origins?: string[];
  tracker_origins?: string[];
  category?: string;
  client_id?: string;
  preset?: 'newznab' | 'torznab' | 'prowlarr' | 'jackett';
  remote_path?: string;
  local_path?: string;
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
  options?: { selection?: string; query?: string; signal?: AbortSignal },
): Promise<AcquisitionCatalog> {
  const params = new URLSearchParams({ connection });
  if (options?.selection) params.set('selection', options.selection);
  if (options?.query) params.set('q', options.query);
  return apiGet<AcquisitionCatalog>(`${BASE}/acquisition/catalog?${params.toString()}`, { signal: options?.signal });
}

export function getAcquisitionJobs(): Promise<{ jobs: AcquisitionJob[] }> {
  return apiGet<{ jobs: AcquisitionJob[] }>(`${BASE}/acquisition/jobs`);
}

export function getAcquisitionBundleChoices(jobId: string): Promise<AcquisitionBundleChoices> {
  return apiGet<AcquisitionBundleChoices>(`${BASE}/acquisition/jobs/${encodeURIComponent(jobId)}/books`);
}

export function selectAcquisitionBundleBook(
  jobId: string,
  body: { generation: string; candidate_id: string },
): Promise<AcquisitionJob> {
  return apiPost<AcquisitionJob>(`${BASE}/acquisition/jobs/${encodeURIComponent(jobId)}/books`, body);
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
  adapter = 'opds',
): Promise<AcquisitionConnection> {
  return apiPost<AcquisitionConnection>(`${BASE}/admin/acquisition/connections`, {
    label, adapter, config,
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

export function getAcquisitionConnection(id: string): Promise<AcquisitionConnection & { config: AcquisitionConnectionInput & { has_secret: boolean; private_origins?: string[] } }> {
  return apiGet(`${BASE}/admin/acquisition/connections/${encodeURIComponent(id)}`);
}

export function editAcquisitionConnection(id: string, label: string, config: Partial<AcquisitionConnectionInput>, expected_revision?: number): Promise<{ ok: true }> {
  return apiPatch(`${BASE}/admin/acquisition/connections/${encodeURIComponent(id)}`, { label, config, expected_revision });
}

export function deleteAcquisitionConnection(id: string): Promise<{ ok: true }> {
  return apiDelete(`${BASE}/admin/acquisition/connections/${encodeURIComponent(id)}`);
}

export function rejectAcquisitionJob(id: string): Promise<{ ok: true }> {
  return apiPost(`${BASE}/admin/acquisition/jobs/${encodeURIComponent(id)}/reject`);
}
