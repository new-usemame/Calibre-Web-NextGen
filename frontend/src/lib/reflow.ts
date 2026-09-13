/* Reflow API layer — the money endpoints under /api/v1/books/<id>/reflow.
 *
 * Kept out of queries.ts because everything here is about one page and one
 * transaction: an estimate the user is shown, a consent they give to a figure,
 * and a job that is watched until it stops. Shapes mirror cps/api/reflow.py. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPost } from './api';

export type ReflowTier = 'cheap' | 'standard' | 'quality';
export type ReflowMode = 'sample' | 'full';

export interface ReflowTierChoice {
  tier: string;
  model: string;
  label: string;
  price_per_page: number;
}

export interface ReflowEstimate {
  book_id: number;
  title: string;
  /** One of assess.VERDICTS. The sentence a reader sees is translated in the
   *  page — the server's copy of it is written into the EPUB's own report,
   *  which travels with the book and is not in the reader's language. */
  verdict: string;
  pages: number;
  text_layer: boolean;
  routed_pages_estimate: number;
  routed_share: number;
  estimate_usd: Record<string, number>;
  worst_case_usd: Record<string, number>;
  target_usd: number;
  over_target: boolean;
  sample_suggested: boolean;
  existing_epub: boolean;
  configured: boolean;
  default_tier: string;
  hard_cap_usd: number;
  sample_pages_default: number;
  sample_pages_max: number;
  tiers: ReflowTierChoice[];
  priced_on: string;
  sampled: number;
  reasons: Record<string, number>;
  cached: boolean;
}

export interface ReflowJob {
  job_id: string;
  mode: ReflowMode;
  /** ``capped`` is a success that stopped early: the pages bought before the
   *  cap was reached were written, the rest were not. See cps/tasks/reflow.py. */
  status: 'waiting' | 'running' | 'done' | 'capped' | 'failed' | 'cancelled';
  started: number | null;
  finished: number | null;
  spend_usd: number;
  cap_usd: number;
  pages: number;
  calls: number;
  reused: number;
  gate: Record<string, number>;
  models: Record<string, number>;
  error: string | null;
  sample_url: string | null;
  sample_ready?: boolean;
}

export interface ReflowActive {
  task_id: string;
  job_id: string | null;
  status: string;
  progress: number;
  message: string;
  cancellable: boolean;
}

export interface ReflowJobs {
  items: ReflowJob[];
  active: ReflowActive[];
}

export interface ReflowStartBody {
  mode: ReflowMode;
  model_tier: string;
  sample_pages?: number;
  cost_cap_usd: number;
  consent: true;
  replace_existing_epub?: boolean;
  include_report_page?: boolean;
}

export interface ReflowStarted {
  task_id: number | string;
  job_id: string;
  mode: ReflowMode;
  model_tier: string;
  cost_cap_usd: number;
  estimate_usd: number;
}

/** The deterministic pass reads the PDF, so this is slow the first time and
 *  cached on the server afterwards. Never refetched on focus: re-reading a
 *  700-page PDF because somebody switched tabs is not free of CPU. */
export function useReflowEstimate(id: string | number) {
  return useQuery<ReflowEstimate>({
    queryKey: ['reflow-estimate', String(id)],
    queryFn: () => apiGet<ReflowEstimate>(`/api/v1/books/${id}/reflow/estimate`),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

/** Polls only while the worker has this book in hand.
 *
 *  A conversion is watched for as long as an hour, so the poll cannot be a
 *  constant: a page left open on a finished book would ask a server question
 *  every two seconds forever. Stopping the moment ``active`` empties is safe
 *  because the task writes its finish record to the ledger BEFORE it leaves the
 *  worker's list, so the response that first shows nothing active already
 *  carries the final row. */
export function useReflowJobs(id: string | number) {
  return useQuery<ReflowJobs>({
    queryKey: ['reflow-jobs', String(id)],
    queryFn: () => apiGet<ReflowJobs>(`/api/v1/books/${id}/reflow/jobs`),
    refetchInterval: (query) => (query.state.data?.active.length ? 2000 : false),
  });
}

export function useStartReflow(id: string | number) {
  const qc = useQueryClient();
  return useMutation<ReflowStarted, unknown, ReflowStartBody>({
    mutationFn: (body: ReflowStartBody) =>
      apiPost<ReflowStarted>(`/api/v1/books/${id}/reflow`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['reflow-jobs', String(id)] });
      void qc.invalidateQueries({ queryKey: ['book', String(id)] });
    },
  });
}

export function useCancelReflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: number | string) =>
      apiPost(`/api/v1/tasks/${encodeURIComponent(String(taskId))}/cancel`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['reflow-jobs'] }),
  });
}

/** USD, always two decimals and always with the sign the consent line quotes. */
export function usd(amount: number): string {
  return `$${(Math.round((amount + Number.EPSILON) * 100) / 100).toFixed(2)}`;
}

/** How many pages of a *pages*-page sample would reach the model, mirroring
 *  cps/api/reflow.py::_sample_share. */
export function sampleRoutedPages(est: ReflowEstimate, pages: number): number {
  const total = Math.max(1, est.pages || 1);
  const routed = est.routed_pages_estimate || 0;
  return Math.max(1, Math.round(routed * (Math.min(pages, total) / total)));
}

/** The figure the consent line quotes.
 *
 *  Deliberately the server's arithmetic, not an approximation of it: the start
 *  endpoint refuses a cap below this number, and a page that quotes one figure
 *  and is refused for another is a page nobody can get past. */
export function requiredUsd(est: ReflowEstimate, tier: string, mode: ReflowMode,
                            samplePages: number): number {
  if (mode === 'sample') {
    const spec = est.tiers.find((choice) => choice.tier === tier);
    const price = spec?.price_per_page ?? 0;
    return Math.round(price * sampleRoutedPages(est, samplePages) * 10000) / 10000;
  }
  return Math.round((est.estimate_usd[tier] ?? 0) * 10000) / 10000;
}

/** The cap a job starts with: the estimate plus a quarter, so a book that runs
 *  a little dearer than its sample finishes instead of stopping at 98%. */
export function suggestedCap(estimate: number, hardCap: number): number {
  const padded = Math.ceil(estimate * 1.25 * 100) / 100;
  return Math.min(Math.max(padded, 0.01), hardCap);
}
