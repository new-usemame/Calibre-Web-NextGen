/** Current typed review: actual source-bound wire ceilings, never projected prices. */
import type { ReflowEstimate, ReflowMode } from './reflowMoney.ts';
export type ReviewMode = 'deterministic' | 'source_verified';
export type SourceRecovery = 'auto' | 'textless' | 'off';
export type SourceAssessment = Pick<ReflowEstimate,
  'book_id' | 'title' | 'verdict' | 'pages' | 'text_layer' | 'sample_suggested' |
  'existing_epub' | 'configured' | 'hard_cap_usd' | 'sample_pages_default' |
  'sample_pages_max' | 'sampled' | 'cached' | 'recovery'> & {
    source_sha256: string;
    consent_contract: string;
    review: { quality_released: boolean; route_version: string; source_revision: string;
      provider: string; service_tier: string; proposer: string; verifier: string; max_output_tokens: number };
  };
export interface ReviewQuote {
  identity: string; source_sha256: string; source_context_pages: number; first_body_page: number;
  eligible_pages: number; limited_pages: number; unsupported_pages: number; no_choice_pages: number;
  full_bound_usd: number; proposer_bound_usd: number; verifier_bound_usd: number;
  confirmed_usd: number; held_usd: number;
  pages: { page_index0: number; proposer_bound_usd: number; verifier_bound_usd: number }[];
  coverage: { page: number; status: string; reason?: string }[];
}
export interface ReviewPreparation {
  preparation_id: string;
  status: 'waiting' | 'preparing' | 'cancelling' | 'cancelled' | 'ready' | 'failed';
  progress: { stage?: string; page?: number; pages?: number };
  quote?: ReviewQuote; error?: string;
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
export function selectedReview(quote: ReviewQuote | undefined, mode: ReflowMode, samplePages: number) {
  const start = mode === 'sample' ? (quote?.first_body_page ?? 0) : 0;
  const end = Math.min(quote?.source_context_pages ?? 0, mode === 'sample' ? start + samplePages : Infinity);
  const rows = quote?.pages.filter((p) => p.page_index0 >= start && p.page_index0 < end) ?? [];
  const coverage = quote?.coverage.filter((p) => p.page >= start && p.page < end) ?? [];
  return { start, end, eligible: rows.length,
    bound: rows.reduce((sum, p) => sum + p.proposer_bound_usd + p.verifier_bound_usd, 0),
    limited: coverage.filter((p) => p.status === 'limited').length,
    unsupported: coverage.filter((p) => p.status === 'unsupported').length,
    noChoices: coverage.filter((p) => p.status === 'no_choices').length };
}
