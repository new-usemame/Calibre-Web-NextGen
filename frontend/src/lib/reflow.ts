/* Reflow API layer — the money endpoints under /api/v1/books/<id>/reflow.
 *
 * Kept out of queries.ts because everything here is about one page and one
 * transaction: an estimate the user is shown, a consent they give to a figure,
 * and a job that is watched until it stops. Shapes mirror cps/api/reflow.py. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPost } from './api';

import type { ReflowEstimate, ReflowMode } from './reflowMoney.ts';

// The shapes and the sums are one subject with the endpoints, and every caller
// reaches them through this module; only the file they are written in moved.
export type {
  ReflowEstimate, ReflowMode, ReflowTier, ReflowTierChoice,
} from './reflowMoney.ts';
export {
  requiredUsd, routedPagesAreProjected, sampleRoutedPages, suggestedCap, usd,
} from './reflowMoney.ts';

export interface ReflowJob {
  job_id: string;
  mode: ReflowMode;
  /** ``capped`` and ``incomplete`` both stopped early with a file in hand: the
   *  pages bought before the stop were written, the rest were not. The first is
   *  the cap the user set, the second is the model service going away mid-book.
   *  Only ``done`` is a conversion that did what it was asked for.
   *  See STOP_STATUS in cps/tasks/reflow.py. */
  status: 'waiting' | 'running' | 'done' | 'capped' | 'incomplete' | 'failed'
    | 'cancelled';
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

