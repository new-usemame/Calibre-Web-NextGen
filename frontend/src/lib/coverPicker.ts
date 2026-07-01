/* Cover-picker API layer.
 *
 * The cover picker reuses the focused blueprint at /book/<id>/cover/* (candidates,
 * preview, apply, lock, ereader-preview, state) rather than /api/v1 — those
 * endpoints already return JSON and single-source the provider pool, SSRF guard
 * and save path with the legacy picker. They use a {ok,error_code,error_message}
 * / {valid,...} envelope (not the /api/v1 {error:{…}} shape), so these wrappers
 * parse that here. CSRF + session cookie are reused from api.ts. */
import { useQuery } from '@tanstack/react-query';
import { ApiError, getCsrf, apiUrl } from './api';

// ---- shapes (mirror cps/services/cover_picker.py + cover_url_validator.py) ----

export interface CoverCandidate {
  source_id: string;
  source_name: string;
  cover_url: string;
  title: string | null;
  authors: string[] | null;
  publisher: string | null;
  year: string | null;
  width: number | null;
  height: number | null;
  candidate_id: string | null;
  flags: string[] | null;
}

export type ProviderStatusKind =
  | 'ok' | 'empty' | 'error' | 'disabled' | 'missing_key' | 'rate_limited' | 'blocked';

export interface ProviderStatus {
  id: string;
  name: string;
  status: ProviderStatusKind;
  count: number;
  message: string;
  duration_ms: number;
}

export interface CandidatesResponse {
  candidates: CoverCandidate[];
  providers: ProviderStatus[];
  query: string;
}

export interface UrlValidation {
  valid: boolean;
  url: string;
  error_code: string | null;
  error_message: string | null;
  content_type: string | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
}

export interface CoverState {
  locked: boolean;
  ereader_enabled: boolean;
  ereader_defaults: { aspect: string; fill_mode: string; color: string };
}

export interface ApplyResult { ok: boolean; cover_url?: string; error_message?: string }

export interface ProviderKey {
  id: string;
  name: string;
  configured: boolean;
  can_edit: boolean;
}

export interface EreaderOptions {
  aspect: string;
  fill_mode: string;
  color: string;
}

// Static enums mirrored from cover_picker.html (server defaults come from /state).
export const EREADER_ASPECTS: { value: string; label: string }[] = [
  { value: 'kobo_libra_color', label: 'Kobo Libra Color / Libra 2 (1264×1680)' },
  { value: 'kobo_clara', label: 'Kobo Clara HD / 2E / BW / Colour (1072×1448)' },
  { value: '3:4', label: '3:4 (generic)' },
  { value: '2:3', label: '2:3 (publisher cover — no padding for typical covers)' },
];
export const EREADER_FILL_MODES: { value: string; label: string }[] = [
  { value: 'edge_mirror', label: 'Edge mirror — extend the artwork (recommended)' },
  { value: 'edge_blur', label: 'Edge blur — soft bokeh border' },
  { value: 'gradient', label: 'Gradient — palette-matched top/bottom blend' },
  { value: 'dominant', label: 'Solid: most-common colour in the cover' },
  { value: 'average', label: 'Solid: average colour of the cover' },
  { value: 'manual', label: 'Solid: custom colour' },
];

// ---- low-level fetch (CSRF + envelope parsing) -------------------------------

/** Read the {ok:false,error_message} / {error_message} envelope these endpoints
 *  use, falling back to HTTP status text. */
async function envelopeError(res: Response): Promise<never> {
  let msg = res.statusText;
  try {
    const d = await res.json() as { error_message?: string; error?: string | { message?: string } };
    if (d.error_message) msg = d.error_message;
    else if (typeof d.error === 'string') msg = d.error;
    else if (d.error?.message) msg = d.error.message;
  } catch { /* non-JSON (e.g. HTML 400 from a stale CSRF) — keep statusText */ }
  throw new ApiError(res.status, msg);
}

async function cpGet<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { credentials: 'include', headers: { Accept: 'application/json' } });
  if (!res.ok) return envelopeError(res);
  return res.json() as Promise<T>;
}

/** POST JSON with the same one-shot CSRF refresh-and-retry as api.apiPost. */
async function cpPostJson<T>(path: string, body: unknown): Promise<T> {
  const doPost = (csrf: string) => fetch(apiUrl(path), {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify(body ?? {}),
  });
  let res = await doPost(await getCsrf());
  if (res.status === 400 && !(res.headers.get('content-type') || '').includes('application/json')) {
    res = await doPost(await getCsrf()); // stale token → HTML 400; refresh once
  }
  if (!res.ok) return envelopeError(res);
  return res.json() as Promise<T>;
}

/** POST multipart (file upload) — browser sets the boundary, so no Content-Type. */
async function cpUpload<T>(path: string, form: FormData): Promise<T> {
  const doPost = (csrf: string) => fetch(apiUrl(path), {
    method: 'POST', credentials: 'include', headers: { 'X-CSRFToken': csrf }, body: form,
  });
  let res = await doPost(await getCsrf());
  if (res.status === 400 && !(res.headers.get('content-type') || '').includes('application/json')) {
    res = await doPost(await getCsrf());
  }
  if (!res.ok) return envelopeError(res);
  return res.json() as Promise<T>;
}

const base = (id: string | number) => `/book/${id}/cover`;

// ---- raw calls --------------------------------------------------------------

export const coverApi = {
  state: (id: string | number) => cpGet<CoverState>(`${base(id)}/state`),
  candidates: (id: string | number, query?: string) =>
    cpPostJson<CandidatesResponse>(`${base(id)}/candidates`, query ? { query } : {}),
  validate: (id: string | number, url: string) =>
    cpPostJson<UrlValidation>(`${base(id)}/preview`, { url }),
  applyUrl: (id: string | number, url: string) =>
    cpPostJson<ApplyResult>(`${base(id)}/apply`, { kind: 'url', url }),
  applyEmbedded: (id: string | number) =>
    cpPostJson<ApplyResult>(`${base(id)}/apply`, { kind: 'embedded' }),
  applyFile: (id: string | number, file: File) => {
    const fd = new FormData(); fd.append('file', file);
    return cpUpload<ApplyResult>(`${base(id)}/apply`, fd);
  },
  setLock: (id: string | number, locked: boolean) =>
    cpPostJson<{ locked: boolean }>(`${base(id)}/lock`, { locked }),
  ereaderPreview: (
    id: string | number,
    opts: EreaderOptions & { candidate_url?: string; embedded?: boolean },
  ) => cpPostJson<{ ok: boolean; data_url: string }>(`${base(id)}/ereader-preview`, opts),
  keysList: () => cpGet<ProviderKey[]>(`/metadata/keys`),
  saveKey: (provId: string, value: string) =>
    cpPostJson<{ id: string; configured: boolean }>(`/metadata/keys/${encodeURIComponent(provId)}`, { value }),
};

// ---- hooks ------------------------------------------------------------------

export function useCoverState(id: string) {
  return useQuery({ queryKey: ['cover-state', id], queryFn: () => coverApi.state(id) });
}

export function useCandidates(id: string) {
  return useQuery({
    queryKey: ['cover-candidates', id],
    queryFn: () => coverApi.candidates(id),
    staleTime: 60_000, // provider fan-out is slow; don't refetch on remount
    refetchOnWindowFocus: false,
  });
}

export function useProviderKeys(enabled: boolean) {
  return useQuery({ queryKey: ['cover-keys'], queryFn: () => coverApi.keysList(), enabled });
}
