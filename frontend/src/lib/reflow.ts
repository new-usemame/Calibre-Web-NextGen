/* Reflow API layer — the money endpoints under /api/v1/books/<id>/reflow.
 *
 * Kept out of queries.ts because everything here is about one page and one
 * transaction: an estimate the user is shown, a consent they give to a figure,
 * and a job that is watched until it stops. Shapes mirror cps/api/reflow.py. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPost, apiDelete } from './api';

import type { ReflowMode } from './reflowMoney.ts';
import { preparationActive, type SourceAssessment, type ReviewMode, type ReviewPreparation, type SourceRecovery, type StructuralSummary } from './sourceReview.ts';
export { selectedReview, preparationActive, preparationNote } from './sourceReview.ts';
export type { ReviewMode, ReviewQuote, ReviewPreparation, SourceAssessment } from './sourceReview.ts';

// The shapes and the sums are one subject with the endpoints, and every caller
// reaches them through this module; only the file they are written in moved.
export type {
  ReflowEstimate, ReflowJobCounts, ReflowMode, ReflowTier, ReflowTierChoice,
} from './reflowMoney.ts';
export {
  consentUsd, heldUsd, holdRequiringAcknowledgment, jobCounts, ledgerUsd, requiredUsd,
  routedPagesAreProjected, sampleRoutedPages, suggestedCap, usd,
} from './reflowMoney.ts';

export interface ReflowJob {
  job_id: string;
  mode: ReflowMode;
  /** ``capped`` and ``incomplete`` both stopped early with a file in hand: the
   *  approved changes before the stop were written; all selected pages retain the complete source conversion. The first is
   *  the cap the user set, the second is the model service going away mid-book.
   *  ``billing_unknown`` stopped when a dispatched request's billing could not
   *  be proven either way: its bound stays held, and ``pending_usd`` says how
   *  much. ``interrupted`` is a restart: the process died mid-conversion and
   *  startup recovery settled the record without claiming publication or
   *  resolving unknown charges. A fresh start may reuse cached pages.
   *  Only ``done`` is a conversion that
   *  did what it was asked for.
   *  See STOP_STATUS in cps/tasks/reflow.py. */
  status: 'waiting' | 'running' | 'done' | 'capped' | 'incomplete' | 'failed'
    | 'cancelled' | 'billing_unknown' | 'interrupted' | 'limited';
  started: number | null;
  finished: number | null;
  /** Confirmed spend, reconciled against the provider. */
  spend_usd: number;
  /** Possible charge held because an answer was lost: money the provider may
   *  still bill. Kept out of ``spend_usd`` on purpose; 0 means resolved. */
  pending_usd: number;
  cap_usd: number;
  pages: number;
  calls: number;
  reused: number;
  gate: Record<string, number>;
  models: Record<string, number>;
  /** The source-recovery summary recorded with the job (empty object when the
   *  job predates recovery or none was needed). */
  recovery: {
    attempted?: number;
    reused?: number;
    failed?: number;
    ocr_words?: number;
    uncertain_words?: number;
    mode_pages?: number;
    seconds?: number;
    engine_unavailable?: boolean;
  };
  error: string | null;
  sample_url: string | null;
  sample_ready?: boolean;
  structural?: StructuralSummary | null;
  artifact?: { sha256: string; bytes: number } | null;
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
  review_mode: ReviewMode;
  consent_contract: string;
  source_sha256: string;
  preparation_id?: string;
  sample_pages?: number;
  cost_cap_usd: number;
  consent: true;
  replace_existing_epub?: boolean;
  include_report_page?: boolean;
  source_recovery?: 'auto' | 'textless' | 'off';
  ocr_language?: string;
}

export interface ReflowStarted {
  task_id: number | string;
  job_id: string;
  mode: ReflowMode;
  review_mode: ReviewMode;
  cost_cap_usd: number;
  reservation_ceiling_usd: number;
  partial_review_possible: boolean;
}

/** The deterministic pass reads the PDF, so this is slow the first time and
 *  cached on the server afterwards. Never refetched on focus: re-reading a
 *  700-page PDF because somebody switched tabs is not free of CPU. */
export function useReflowEstimate(id: string | number) {
  return useQuery<SourceAssessment>({
    queryKey: ['reflow-estimate', String(id)],
    queryFn: () => apiGet<SourceAssessment>(`/api/v1/books/${id}/reflow/estimate`),
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


export function usePrepareReflow(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (options: { source_recovery: SourceRecovery; ocr_language: string }) =>
      apiPost<ReviewPreparation>(`/api/v1/books/${id}/reflow/estimate/prepare`, options),
    onSuccess: (data) => qc.setQueryData(['reflow-preparation', String(id), data.preparation_id], data),
  });
}
export function useReflowPreparation(id: string | number, preparationId?: string) {
  return useQuery<ReviewPreparation>({
    queryKey: ['reflow-preparation', String(id), preparationId],
    queryFn: () => apiGet(`/api/v1/books/${id}/reflow/estimate/preparations/${preparationId}`),
    enabled: !!preparationId, retry: false, refetchOnWindowFocus: false,
    refetchInterval: (q) => preparationActive(q.state.data?.status) ? 1000 : false,
  });
}
export function useCancelPreparation(id: string | number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preparationId: string) => apiDelete<ReviewPreparation>(`/api/v1/books/${id}/reflow/estimate/preparations/${preparationId}`),
    onSuccess: (data) => qc.setQueryData(['reflow-preparation', String(id), data.preparation_id], data),
  });
}
