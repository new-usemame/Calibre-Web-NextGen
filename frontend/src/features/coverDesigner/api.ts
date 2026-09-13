/* Cover designer v2 API — catalogue, preset CRUD, preview, apply.
 *
 * Mirrors the endpoints in state/cover-designer/v2/CONTRACT.md. Same transport
 * rules as the rest of the cover picker (session cookie + one-shot CSRF retry,
 * see frontend/src/lib/coverPicker.ts); the error envelope here additionally
 * understands the v2 {"error","message"} shape the contract specifies.
 */
import { ApiError, apiUrl, getCsrf } from '../../lib/api';
import type {
  CoverDesign, DesignerCatalogue, PresetListResponse, PresetResponse, PreviewResponse,
} from './contract';

async function envelopeError(res: Response): Promise<never> {
  let msg = res.statusText;
  try {
    const d = await res.json() as { error_message?: string; error?: string | { message?: string }; message?: string };
    if (typeof d.message === 'string' && d.message) msg = d.message;
    else if (d.error_message) msg = d.error_message;
    else if (typeof d.error === 'string') msg = d.error;
    else if (d.error?.message) msg = d.error.message;
  } catch { /* non-JSON (e.g. HTML 400 from a stale CSRF) — keep statusText */ }
  throw new ApiError(res.status, msg);
}

async function cdGet<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), { credentials: 'include', headers: { Accept: 'application/json' } });
  if (!res.ok) return envelopeError(res);
  return res.json() as Promise<T>;
}

async function cdJson<T>(method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const send = (csrf: string) => fetch(apiUrl(path), {
    method, credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let res = await send(await getCsrf());
  if (res.status === 400 && !(res.headers.get('content-type') || '').includes('application/json')) {
    res = await send(await getCsrf()); // stale token → HTML 400; refresh once
  }
  if (!res.ok) return envelopeError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const bookBase = (id: string | number) => `/book/${id}/cover`;
const personalBase = (id: string | number) => `/api/v1/books/${id}/my-cover`;

export interface ApplyResult { ok?: boolean; cover_url?: string; error_message?: string }

export const coverDesignerApi = {
  /** Standalone catalogue fetch. The picker normally gets the catalogue inside
   *  the cover-state payload; this exists for refresh and for hosts that only
   *  implement the contract's standalone route. */
  catalogue: () => cdGet<DesignerCatalogue>(`/cover-designer/catalogue`),

  presets: () => cdGet<PresetListResponse>(`/cover-designer/presets`),
  createPreset: (name: string, design: CoverDesign, scope?: 'user' | 'library') =>
    cdJson<PresetResponse>('POST', `/cover-designer/presets`, { name, design, ...(scope ? { scope } : {}) }),
  updatePreset: (id: string, patch: { name?: string; design?: CoverDesign }) =>
    cdJson<PresetResponse>('PUT', `/cover-designer/presets/${encodeURIComponent(id)}`, patch),
  deletePreset: (id: string) =>
    cdJson<void>('DELETE', `/cover-designer/presets/${encodeURIComponent(id)}`),
  restorePreset: (id: string) =>
    cdJson<PresetResponse>('POST', `/cover-designer/presets/${encodeURIComponent(id)}/restore`, {}),
  /** The reader's own ordering of saved presets; returns the refreshed list. */
  reorderPresets: (order: string[]) =>
    cdJson<PresetListResponse>('POST', `/cover-designer/presets/order`, { order }),

  designPreview: (id: string | number, design: CoverDesign, personal = false) =>
    cdJson<PreviewResponse & { ok?: boolean }>(
      'POST',
      `${bookBase(id)}/design-preview${personal ? '?scope=personal' : ''}`,
      { design },
    ),

  applyGenerated: (id: string | number, design: CoverDesign, personal = false) =>
    personal
      ? cdJson<ApplyResult>('PUT', personalBase(id), { kind: 'generated', design })
      : cdJson<ApplyResult>('POST', `${bookBase(id)}/apply`, { kind: 'generated', design }),
};
