import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { ReflowEstimate, ReflowJob } from '../src/lib/reflow';
import { requireRouteCapability } from './capabilities';
import { collectPageErrors, assertNoPageErrors } from './utils';

/**
 * Reflow: the numbers on the page that are about somebody's money.
 *
 *   - "Pages a model will read" is scaled up from a forty-page survey on any book
 *     longer than that, and says so; on a book the survey read entirely it is a
 *     count and is not hedged.
 *   - The consent sentence names the cap, which is what stops a conversion, and
 *     not the estimate the cap was filled in from.
 *   - Moving the cap asks for the agreement again.
 *   - A finished job reports the pages it SENT to a model, which on a resumed job
 *     is not the pages it judged.
 *
 * The arithmetic is unit-tested (frontend/unit/reflowMoney.test.ts). What only a
 * browser can check is the wiring: which sum reaches which label, and whether the
 * consent reset watches the figure the reader is looking at.
 *
 * Both Reflow endpoints are STUBBED. That is not a convenience. It makes the spec
 * seed-independent — no lane is guaranteed a book with a PDF in it — and it means
 * the page is never talking to the converter, so this spec cannot start a
 * conversion or spend anything. The payload is the acceptance book's own estimate,
 * copied from the rig's `/reflow/estimate`: 698 pages, a survey of 40, 384
 * projected, $0.887 at the standard tier, $5.00 administrator ceiling.
 */

/** The rig's own estimate for the acceptance book, field for field. */
function estimatePayload(over: Partial<ReflowEstimate> = {}): ReflowEstimate {
  return {
    book_id: 1,
    title: 'a book somebody is about to pay to convert',
    verdict: 'OCR_LAYER',
    pages: 698,
    text_layer: true,
    routed_pages_estimate: 384,
    routed_share: 0.5501,
    estimate_usd: { cheap: 0.3306, standard: 0.887, quality: 0.887 },
    worst_case_usd: { cheap: 0.601, standard: 1.6124, quality: 1.6124 },
    target_usd: 0.5,
    over_target: true,
    sample_suggested: true,
    existing_epub: false,
    configured: true,
    default_tier: 'standard',
    hard_cap_usd: 5,
    sample_pages_default: 20,
    sample_pages_max: 60,
    tiers: [
      { tier: 'cheap', model: 'a/cheap-model', label: 'Cheapest', price_per_page: 0.000861 },
      { tier: 'standard', model: 'a/standard-model', label: 'Standard', price_per_page: 0.00231 },
      { tier: 'quality', model: 'a/quality-model', label: 'Best quality', price_per_page: 0.00231 },
    ],
    priced_on: '2026-09-13',
    sampled: 40,
    reasons: { note_marker_mismatch: 11 },
    cached: true,
    recovery: {
      ocr_candidates: 0, image_only: 0, damaged: 0, estimated_seconds: 0,
      engine_available: true, engine_version: 'tesseract 5.3.4', engine_detail: '',
      language: 'eng', dpi: 300, pdf_sha256: '0'.repeat(64), non_latin_share: 0,
    },
    ...over,
  };
}

/** The resumed whole-book run of the acceptance report: 210 bought, 212 replayed,
 *  422 judged. Only the first of those three was sent anywhere. */
const RESUMED_JOB: ReflowJob = {
  job_id: '1e485e5c73e24435',
  mode: 'full',
  status: 'done',
  started: 1789375000,
  finished: 1789376331,
  spend_usd: 0.279521,
  pending_usd: 0,
  cap_usd: 0.9,
  pages: 698,
  calls: 210,
  reused: 212,
  gate: { PASS: 223, FAIL: 198, NOT_APPLICABLE: 1 },
  models: { 'a/standard-model': 210 },
  error: null,
  sample_url: null,
  sample_ready: false,
  recovery: {},
};

async function stubReflow(page: Page, estimate: ReflowEstimate,
                          jobs: unknown[] = []) {
  await page.route('**/api/v1/books/*/reflow/estimate*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(estimate),
  }));
  await page.route('**/api/v1/books/*/reflow/jobs*', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: jobs, active: [] }),
  }));
}

/** Any book at all: the page is reached through one, and everything the page asks
 *  the converter is answered by the stubs, so which book it is does not matter. */
async function anyBookId(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const res = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .catch(() => null);
    if (!res || !res.ok) return null;
    const body = await res.json().catch(() => null);
    return body?.items?.[0]?.id ?? null;
  });
}

async function openReflow(page: Page) {
  // Let the catalog's auth/browse requests settle before navigating away;
  // Rapid navigation can report cancelled requests as access-control errors.
  await page.goto('/app', { waitUntil: 'networkidle' });
  const book = await anyBookId(page);
  test.skip(book == null, 'this lane has no books at all');
  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${book}/reflow`, { waitUntil: 'domcontentloaded' });
  return { book, errors };
}

test.describe('Reflow quotes money the reader can hold it to', () => {
  test.beforeEach(async ({ request }) => {
    await requireRouteCapability(request, {
      method: 'POST',
      path: '/api/v1/books/1/reflow',
      name: 'Reflow PDF to EPUB',
      pinnedBy: 'tests/unit/test_reflow_api.py',
    });
  });

  test('an interrupted job exposes small charges and readable recovery in both themes',
       async ({ page }) => {
         await page.emulateMedia({ reducedMotion: 'reduce' });
         await stubReflow(page, estimatePayload(), [{
           ...RESUMED_JOB, status: 'interrupted', mode: 'sample',
           spend_usd: 0.0022, pending_usd: 0.0217, calls: 1,
           error: 'The application restarted before this conversion finished.',
         }]);
         const { errors } = await openReflow(page);
         const result = page.locator('section[aria-labelledby="reflow-result"]');
         await expect(result.getByRole('alert')).toBeVisible();
         await expect(result.getByRole('status')).toBeVisible();
         await expect(result.getByText('$0.0022', { exact: true })).toBeVisible();
         await expect(result.getByText('$0.0217', { exact: true })).toBeVisible();
         await expect(page.locator('label').filter({
           hasText: 'I understand the unconfirmed $0.0217',
         }).getByRole('checkbox')).toBeVisible();
         for (const theme of ['dark', 'light']) {
           await page.evaluate(async (value) => {
             document.documentElement.setAttribute('data-theme', value);
             await new Promise<void>((resolve) => requestAnimationFrame(() =>
               requestAnimationFrame(() => resolve())));
           }, theme);
           const results = await new AxeBuilder({ page })
             .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
             .analyze();
           expect(results.violations.filter((v) =>
             v.impact === 'serious' || v.impact === 'critical'),
           `Interrupted conversion / ${theme}`).toEqual([]);
         }
         assertNoPageErrors(errors);
       });

  test('a page count scaled up from a survey is shown as an estimate, and a counted one is not',
       async ({ page }) => {
         await stubReflow(page, estimatePayload());
         const { errors } = await openReflow(page);

         const assessment = page.locator('section[aria-labelledby="reflow-assessment"]');
         await expect(assessment).toBeVisible();
         // 384 is 55% of 698 scaled off 40 sampled pages. The reader is told both.
         await expect(assessment).toContainText('about 384 (55%)');
         await expect(assessment).toContainText('40 pages spread through the book');

         // A book the survey read end to end: there the count is a count.
         await stubReflow(page, estimatePayload({
           pages: 30, sampled: 30, routed_pages_estimate: 12, routed_share: 0.4,
           estimate_usd: { cheap: 0.0103, standard: 0.0277, quality: 0.0277 },
         }));
         await page.reload({ waitUntil: 'domcontentloaded' });
         await expect(assessment).toContainText('12 (40%)');
         await expect(assessment).not.toContainText('about 12');
         await expect(assessment).not.toContainText('spread through the book');
         assertNoPageErrors(errors);
       });

  test('the consent line names the cap, and moving the cap asks again',
       async ({ page }) => {
         await stubReflow(page, estimatePayload());
         const { errors } = await openReflow(page);

         const consent = page.locator('label').filter({ hasText: 'I agree to spend up to' });
         const box = consent.locator('input[type=checkbox]');
         const capField = page.locator('label').filter({ hasText: 'Stop after spending' })
           .locator('input[type=number]');
         const convert = page.getByRole('button', { name: 'Convert the book' });

         // The book is over target, so it opens on the sample. The figures below are
         // the whole book's.
         await page.locator('label').filter({ hasText: 'The whole book' }).click();

         // $0.887 estimated, so the cap the page fills in is a quarter more.
         await expect(capField).toHaveValue('1.11');
         await expect(consent).toContainText('$1.11');
         await expect(consent).not.toContainText('$0.89');

         await box.check();
         await expect(convert).toBeEnabled();

         // Raise the ceiling and the agreement is to a different number, so it has to
         // be given again: a tick taken at $1.11 must not survive a cap raised to the
         // administrator's $5.00.
         await capField.fill('5.00');
         await expect(consent).toContainText('$5.00');
         await expect(box).not.toBeChecked();
         await expect(convert).toBeDisabled();
         await box.check();
         await expect(convert).toBeEnabled();

         // Below the estimate: refused, with the reason said out loud.
         await capField.fill('0.10');
         await expect(page.getByText('That is below the estimate', { exact: false })).toBeVisible();
         await expect(consent).toContainText('$0.10');
         await expect(convert).toBeDisabled();
         assertNoPageErrors(errors);
       });

  test('a resumed job reports the pages it bought, not the pages it judged',
       async ({ page }) => {
         await stubReflow(page, estimatePayload(), [RESUMED_JOB]);
         const { errors } = await openReflow(page);

         const result = page.locator('section[aria-labelledby="reflow-result"]');
         await expect(result).toBeVisible();
         const facts = await result.locator('dl').innerText();
         // 210 bought + 212 replayed = the 422 pages judged, of which 223 were the
         // model's. The number that must not stand beside "sent to a model" is 422,
         // which is what reading the gate totals as a purchase produces.
         expect(facts).toContain('210');
         expect(facts).toContain('212');
         expect(facts).toContain('223 (53%)');
         expect(facts).not.toContain('422');
         // And the refusals are reported as what they are: the converter's own
         // reading kept, with nothing rewritten (DECISIONS R3).
         await expect(result).toContainText('199 pages did not pass the word check');
         await expect(result).toContainText('Nothing was rewritten');
         assertNoPageErrors(errors);
       });
});
