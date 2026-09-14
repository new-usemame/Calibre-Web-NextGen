/* What a conversion costs, and the shapes a price is quoted in.
 *
 * Split out of reflow.ts so it can be read and tested without the query layer:
 * these four sums are a second implementation of arithmetic cps/api/reflow.py also
 * does, and the server refuses a start whose cap falls below its own figure. The
 * endpoints live next door in reflow.ts, which re-exports all of this. */

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

/** USD, always two decimals and always with the sign the consent line quotes. */
export function usd(amount: number): string {
  return `$${(Math.round((amount + Number.EPSILON) * 100) / 100).toFixed(2)}`;
}

/** Whether "pages a model will read" is a measurement or a projection.
 *
 *  `pipeline.survey` runs the deterministic pass over at most SURVEY_PAGES pages
 *  spread through the book and scales their routed share to the whole of it, so on
 *  anything longer than that sample the count is an estimate: on the acceptance
 *  book it projects 384 where the whole-book pass routes 425. The money is padded
 *  for that already (`suggestedCap`), the sentence is not, and `survey` returns
 *  `sampled` for no other reason than to let the page say which it is showing.
 *  A book short enough to be read entirely is not an estimate and is not hedged. */
export function routedPagesAreProjected(est: ReflowEstimate): boolean {
  const sampled = est.sampled || 0;
  return sampled > 0 && sampled < (est.pages || 0);
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
