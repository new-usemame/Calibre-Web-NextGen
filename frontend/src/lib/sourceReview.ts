/** Vision readings: source-bound request ceilings, expected cost and selection scope. */
import type { ReflowEstimate, ReflowMode } from './reflowMoney.ts';
export type ReviewMode = 'deterministic' | 'source_verified';
export type SourceRecovery = 'auto' | 'textless' | 'off';
export type InstanceBudgetStatus = 'available' | 'disabled' | 'exhausted' | 'unavailable';
export type SourceAssessment = Pick<ReflowEstimate,
  'book_id' | 'title' | 'verdict' | 'pages' | 'text_layer' | 'sample_suggested' |
  'existing_epub' | 'configured' | 'hard_cap_usd' | 'sample_pages_default' |
  'sample_pages_max' | 'sampled' | 'cached' | 'recovery'> & {
    /** Basic assessment reads a bounded text-layer survey on long PDFs. Its
     *  recovery totals are projections, unlike a complete assessment. */
    assessment_scope: 'sample' | 'complete';
    source_sha256: string;
    instance_budget: { status: InstanceBudgetStatus; remaining_usd: number | null; window_hours: 24 };
    consent_contract: string;
    review: { quality_released: boolean; route_version: string; source_revision: string;
      provider: string; service_tier: string; model?: string; scope?: string; proposer: string; verifier: string; max_output_tokens: number };
  };
export interface ReviewQuote {
  identity: string; source_sha256: string; source_context_pages: number; first_body_page: number;
  sample_starts?: Record<string, number>;
  eligible_pages: number; limited_pages: number; unsupported_pages: number; no_choice_pages: number;
  full_bound_usd: number; proposer_bound_usd: number; verifier_bound_usd: number;
  primary_bound_usd?: number; retry_bound_usd?: number; marker_bound_usd?: number;
  expected_usd?: number; target_usd?: number;
  confirmed_usd: number; held_usd: number;
  pages: { page_index0: number; proposer_bound_usd: number; verifier_bound_usd: number; full_bound_usd?: number; batch_bound_usd?: number; primary_bound_usd?: number; retry_bound_usd?: number; marker_bound_usd?: number; expected_usd?: number }[];
  coverage: { page: number; status: string; reason?: string }[];
}
export interface ReviewPreparation {
  preparation_id: string;
  status: 'waiting' | 'preparing' | 'cancelling' | 'cancelled' | 'ready' | 'failed';
  progress: { stage?: string; page?: number; pages?: number };
  quote?: ReviewQuote; error?: string;
  /** Preparations that run before this waiting one; the slot is shared, first come first served. */
  ahead?: number;
  /** The wall-clock limit one preparation runs under before it is stopped. */
  timeout_minutes?: number;
}
export interface StructuralSummary {
  review_mode: ReviewMode; eligibility_measured: boolean; requested_models?: Record<string, number>;
  source_context_pages: number; total_pages: number;
  /** The PDF's own page count, and whether a sample read only the front of it. */
  source_pages?: number; context?: 'sample' | 'complete';
  eligible: number; limited: number; unsupported: number; no_choices: number; unreviewed: number;
  proposed_pages: number; proposed_operations: number; approved_pages: number; approved_operations: number;
  approved_heading: number; approved_quote: number; proposer_abstained: number; verifier_abstained: number;
  rejected: number; attempted_stages: number; attempted_pages: number; cached_stages: number;
}
export function preparationActive(status?: ReviewPreparation['status']): boolean {
  return status === 'waiting' || status === 'preparing' || status === 'cancelling';
}
export type PreparationNote =
  | { kind: 'queued'; ahead: number }
  | { kind: 'preparing'; page: number; pages?: number }
  | { kind: 'cancelled' } | { kind: 'timed_out'; minutes: number } | { kind: 'failed' }
  | { kind: 'ready' } | { kind: 'none' };
/** What the preparation status line says: waiting in line for the shared slot,
 *  preparing, or how it ended -- a preparation stopped at its time limit keeps the
 *  pages it recognized and can continue, which a plain failure cannot promise. */
export function preparationNote(prep: ReviewPreparation | undefined, pending: boolean,
  failed: boolean, ready: boolean): PreparationNote {
  if (prep?.status === 'waiting' && (prep.ahead ?? 0) > 0) return { kind: 'queued', ahead: prep.ahead ?? 0 };
  if (pending || preparationActive(prep?.status)) {
    return { kind: 'preparing', page: prep?.progress.page ?? 0, pages: prep?.progress.pages };
  }
  if (prep?.status === 'cancelled') return { kind: 'cancelled' };
  if (prep?.status === 'failed' && prep.error === 'source_preparation_timeout') {
    return { kind: 'timed_out', minutes: prep.timeout_minutes ?? 30 };
  }
  if (prep?.status === 'failed' || failed) return { kind: 'failed' };
  if (ready) return { kind: 'ready' };
  return { kind: 'none' };
}
export function selectedReview(quote: ReviewQuote | undefined, mode: ReflowMode, samplePages: number) {
  const start = mode === 'sample' ? (quote?.sample_starts?.[String(samplePages)] ?? quote?.first_body_page ?? 0) : 0;
  const end = Math.min(quote?.source_context_pages ?? 0, mode === 'sample' ? start + samplePages : Infinity);
  const rows = quote?.pages.filter((p) => p.page_index0 >= start && p.page_index0 < end) ?? [];
  const coverage = quote?.coverage.filter((p) => p.page >= start && p.page < end) ?? [];
  const primary = rows.reduce((sum, p) => sum + (p.primary_bound_usd ?? p.proposer_bound_usd ?? 0), 0);
  const retry = rows.reduce((sum, p) => sum + (p.retry_bound_usd ?? p.verifier_bound_usd ?? 0), 0);
  const markers = rows.reduce((sum, p) => sum + (p.marker_bound_usd ?? p.batch_bound_usd ?? 0), 0);
  const expected = rows.reduce((sum, p) => sum + (p.expected_usd ?? 0), 0);
  return { start, end, eligible: rows.length, primary, retry, markers, expected, target: quote?.target_usd ?? .5,
    bound: rows.reduce((sum, p) => sum + (p.full_bound_usd ?? ((p.primary_bound_usd ?? p.proposer_bound_usd ?? 0) + (p.retry_bound_usd ?? p.verifier_bound_usd ?? 0) + (p.marker_bound_usd ?? p.batch_bound_usd ?? 0))), 0),
    limited: coverage.filter((p) => p.status === 'limited').length,
    unsupported: coverage.filter((p) => p.status === 'unsupported').length,
    noChoices: coverage.filter((p) => p.status === 'no_choices').length };
}
