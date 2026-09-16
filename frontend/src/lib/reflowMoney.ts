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
  /** The source-recovery picture for this book: which pages need local OCR,
   *  whether the engine can do it, and what it costs in local time. */
  recovery: {
    ocr_candidates: number;
    image_only: number;
    damaged: number;
    estimated_seconds: number;
    engine_available: boolean;
    engine_version: string;
    engine_detail: string;
    language: string;
    dpi: number;
    pdf_sha256: string;
    non_latin_share: number;
  };
}

/** The counts a finished job's card shows, kept apart from one another.
 *
 *  Shaped structurally rather than against `ReflowJob` so the sums stay in this
 *  module and `reflow.ts` keeps re-exporting them (it imports this file, not the
 *  other way round). */
export interface ReflowJobLike {
  gate?: Record<string, number> | null;
  calls?: number | null;
  reused?: number | null;
}

export interface ReflowJobCounts {
  /** Pages this run sent to a model and paid for. */
  sent: number;
  /** Pages whose answer was replayed from an earlier run, at no cost. */
  reused: number;
  /** Pages the gate judged: the two above, together. */
  checked: number;
  adopted: number;
  refused: number;
  /** `adopted` as a percentage of `checked`; 0 when nothing was checked. */
  sharePct: number;
}

/** What a job did, with the pages it bought kept apart from the pages it replayed.
 *
 *  `ledger.totals` is explicit that a page served from the cache "is not a call:
 *  counting it would make a resumed job look like it spent again at $0.00 a page",
 *  and it keeps the two in separate fields for that reason. The gate counts do not:
 *  `_adopt` judges a replayed page exactly as it judges a bought one, so
 *  `PASS + FAIL + NOT_APPLICABLE` is every page the conversion reviewed and not
 *  every page it sent anywhere. A card that reads the gate total as "pages sent to
 *  a model" reports 422 on a run that sent 210 — and then says 212 were reused two
 *  lines below it. `calls` is the number that was paid for; the share stays over
 *  everything judged, because that is the share of the *book* the model's version
 *  was used for. A summary from a build that did not report `calls` still knows
 *  what it replayed, and what it bought is the rest. */
export function jobCounts(job: ReflowJobLike): ReflowJobCounts {
  const gate = job.gate || {};
  const adopted = gate.PASS || 0;
  const refused = (gate.FAIL || 0) + (gate.NOT_APPLICABLE || 0);
  const checked = adopted + refused;
  const reused = Math.max(0, job.reused || 0);
  const calls = job.calls;
  const sent = typeof calls === 'number' && Number.isFinite(calls) && calls >= 0
    ? calls : Math.max(0, checked - reused);
  return {
    sent,
    reused,
    checked,
    adopted,
    refused,
    sharePct: checked ? Math.round((adopted / checked) * 100) : 0,
  };
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

/** The figure the consent sentence names: the most this conversion can spend.
 *
 *  Not the estimate. The estimate is a projection off the forty pages `survey`
 *  reads; the number that can actually stop a conversion is the cap in "Stop after
 *  spending", which `cps/tasks/reflow.py` clamps the job to and which G5 refuses to
 *  let a call cross. The page starts that cap a quarter above the estimate on
 *  purpose (`suggestedCap`), and the reader may raise it as far as the
 *  administrator's ceiling — so a sentence that says "up to" and then names the
 *  estimate understates what is being authorised by at least that quarter. On the
 *  acceptance book: "up to $0.89" over a cap of $1.11, or of $5.00 if the reader
 *  raises it. A cap that is not a usable number yet leaves the estimate as the only
 *  figure there is; nothing can be started from that state anyway. */
export function consentUsd(needed: number, capUsd: number): number {
  return Number.isFinite(capUsd) && capUsd > 0 ? capUsd : needed;
}

/** The cap a job starts with: the estimate plus a quarter, so a book that runs
 *  a little dearer than its sample finishes instead of stopping at 98%. */
export function suggestedCap(estimate: number, hardCap: number): number {
  const padded = Math.ceil(estimate * 1.25 * 100) / 100;
  return Math.min(Math.max(padded, 0.01), hardCap);
}

/** The billing fields a job row carries, kept apart from one another. */
export interface ReflowBillingLike {
  status?: string | null;
  spend_usd?: number | null;
  pending_usd?: number | null;
}

/** The amount a job may still be charged, held because an answer was lost.
 *
 *  `ledger.totals` keeps confirmed spend (`spend_usd`) and unresolved liability
 *  (`pending_usd`) in separate fields for exactly this read: a lost reply is not
 *  a confirmed charge and it is not zero. A job row that predates the field, or
 *  one whose liability resolved, holds nothing. */
export function heldUsd(job: ReflowBillingLike): number {
  const pending = job.pending_usd;
  return typeof pending === 'number' && Number.isFinite(pending) && pending > 0
    ? pending : 0;
}

/** The hold a fresh-spend decision must acknowledge, in a newest-first job list.
 *
 *  The first row still holding anything is the liability on the table: the
 *  consent for the next job has to name it, and the start stays blocked until
 *  the reader says they understand it is not part of the new cap. The amount is
 *  the gate, not the status: a row written after the liability resolved reads
 *  0, and 0 means the way is clear -- no row may be treated as if it resolved. */
export function holdRequiringAcknowledgment(jobs: ReflowBillingLike[]): number {
  for (const job of jobs || []) {
    const held = heldUsd(job);
    if (held > 0) return held;
  }
  return 0;
}
