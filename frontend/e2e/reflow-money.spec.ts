import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { ReflowJob, SourceAssessment, ReviewPreparation, ReviewQuote } from '../src/lib/reflow';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow } from './utils';

// Browser contract only. ALL API requests are stubbed, including starts and
// preparation. This drives the compiled product SPA without paid dispatch or a
// dependency on a seeded PDF. Actual routes/source/ledger have backend seam tests.
function assessment(): SourceAssessment {
  return { book_id: 1, title: 'Source conversion example', verdict: 'BORN_DIGITAL', pages: 60,
    text_layer: true, sample_suggested: true, existing_epub: false, configured: true,
    hard_cap_usd: 5, sample_pages_default: 20, sample_pages_max: 60, sampled: 40, cached: true,
    source_sha256: 'a'.repeat(64), consent_contract: 'source-review-1',
    review: { quality_released: true, route_version: 'test-route', source_revision: 'test-source',
      provider: 'openai/flex', service_tier: 'flex', proposer: 'openai/gpt-5.6-luna',
      verifier: 'openai/gpt-5.6-terra', max_output_tokens: 4096 },
    recovery: { ocr_candidates: 0, image_only: 0, damaged: 0, estimated_seconds: 0,
      engine_available: true, engine_version: 'tesseract 5.3.4', engine_detail: '', language: 'eng',
      dpi: 300, pdf_sha256: 'a'.repeat(16), non_latin_share: 0 } };
}
function quote(): ReviewQuote {
  return { identity: 'b'.repeat(64), source_sha256: 'a'.repeat(64), source_context_pages: 60,
    first_body_page: 4, eligible_pages: 3, limited_pages: 1, unsupported_pages: 1, no_choice_pages: 55,
    proposer_bound_usd: .06, verifier_bound_usd: .3, full_bound_usd: .36, confirmed_usd: 0, held_usd: 0,
    pages: [4, 12, 40].map((page_index0) => ({ page_index0, proposer_bound_usd: .02, verifier_bound_usd: .1 })),
    coverage: Array.from({ length: 60 }, (_, page) => ({ page,
      status: [4, 12, 40].includes(page) ? 'eligible' : page === 5 ? 'unsupported' : page === 6 ? 'limited' : 'no_choices' })) };
}
const reviewed: ReflowJob = {
  job_id: 'f'.repeat(32), mode: 'sample', status: 'capped', started: 1, finished: 2,
  spend_usd: .0022, pending_usd: .0217, cap_usd: .1, pages: 60, calls: 3, reused: 2,
  gate: { PASS: 2 }, models: {}, recovery: {}, error: null, sample_url: '/fake-sample.epub', sample_ready: true,
  artifact: { sha256: 'c'.repeat(64), bytes: 12345 },
  structural: { review_mode: 'source_verified', eligibility_measured: true, source_context_pages: 60,
    total_pages: 20, eligible: 8, limited: 1, unsupported: 2, no_choices: 9, unreviewed: 6,
    proposed_pages: 2, proposed_operations: 3, approved_pages: 1, approved_operations: 1,
    approved_heading: 0, approved_quote: 1, proposer_abstained: 0, verifier_abstained: 1,
    rejected: 0, attempted_stages: 3, attempted_pages: 2, cached_stages: 2,
    requested_models: { 'openai/gpt-5.6-luna': 2, 'openai/gpt-5.6-terra': 1 } },
};
async function stub(page: Page, est = assessment(), jobs: ReflowJob[] = []) {
  const state = { starts: [] as Record<string, unknown>[], preparations: 0, cancellations: 0,
    status: 'ready' as ReviewPreparation['status'] };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { items: [] };
    if (path.endsWith('/auth/me')) body = { id: 7, name: 'Reader', locale: 'en', theme: 'dark',
      role: { admin: false, edit: true, download: true, viewer: true }, sidebar: {}, features: {} };
    else if (path.endsWith('/auth/csrf')) body = { csrf_token: 'inert-test-token' };
    else if (path.endsWith('/reflow/estimate')) body = est;
    else if (path.endsWith('/reflow/estimate/prepare') || path.includes('/reflow/estimate/preparations/')) {
      if (route.request().method() === 'POST') state.preparations++;
      if (route.request().method() === 'DELETE') { state.cancellations++; state.status = 'cancelled'; }
      body = { preparation_id: 'd'.repeat(32), status: state.status,
        progress: { stage: 'source_choices', page: 8, pages: 60 },
        ...(state.status === 'ready' ? { quote: quote() } : {}) };
    } else if (path.endsWith('/reflow/jobs')) body = { items: jobs, active: [] };
    else if (path.endsWith('/reflow') && route.request().method() === 'POST') {
      const input = route.request().postDataJSON(); state.starts.push(input);
      body = { task_id: 1, job_id: 'e'.repeat(32), mode: input.mode, review_mode: input.review_mode,
        cost_cap_usd: input.cost_cap_usd, reservation_ceiling_usd: .24, partial_review_possible: true };
    } else if (path.endsWith('/books/1')) body = { id: 1, title: est.title, authors: [], formats: [] };
    else if (path.includes('/notices')) body = { notices: [], summary: { count: 0 } };
    else if (path.includes('/announcement')) body = { announcements: [], active: false };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const errors = collectPageErrors(page);
  await page.goto('/app/book/1/reflow');
  await expect(page.getByRole('heading', { name: 'Reflow PDF to EPUB', exact: true })).toBeVisible();
  return { state, errors };
}
const paidChoice = (page: Page) => page.getByRole('radio', { name: /^Add AI formatting review/ });
const consent = (page: Page) => page.getByRole('checkbox', { name: /^I authorize Luna/ });
async function preparePaid(page: Page) {
  await paidChoice(page).check();
  await page.getByRole('button', { name: 'Prepare AI estimate', exact: true }).click();
  await expect(page.getByText('Source estimate ready. No credit has been reserved.', { exact: true })).toBeVisible();
}

test('keyless source conversion remains usable and cannot silently become paid review', async ({ page }) => {
  const est = assessment(); est.configured = false; est.review.quality_released = false;
  const { state, errors } = await stub(page, est);
  await expect(paidChoice(page)).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Prepare AI estimate' })).toHaveCount(0);
  const free = page.getByRole('checkbox', { name: 'Create this source conversion without model requests or provider charges.' });
  await free.focus(); await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Convert the sample', exact: true }).click();
  await expect.poll(() => state.starts.length).toBe(1);
  expect(state.starts[0]).toMatchObject({ review_mode: 'deterministic', cost_cap_usd: 0,
    consent_contract: 'source-review-1', source_sha256: est.source_sha256 });
  expect(state.starts[0]).not.toHaveProperty('model_tier');
  expect(state.preparations).toBe(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: test.info().outputPath('source-conversion.jpg'), type: 'jpeg', quality: 75, fullPage: true });
  assertNoPageErrors(errors);
});

test('prepared wire ceiling and cap stay distinct; changing cap or scope resets consent', async ({ page }) => {
  const { state, errors } = await stub(page);
  await preparePaid(page);
  const card = page.locator('section[aria-labelledby="reflow-cost"]');
  await expect(card).toContainText('2');
  await expect(card.getByText('$0.24', { exact: true })).toBeVisible();
  await expect(page.getByText(/This cap may cover only part/)).toHaveCount(0);
  const cap = page.getByRole('spinbutton', { name: /Stop after spending/ });
  await cap.focus(); await expect(cap).toBeFocused();
  const inputBox = await cap.boundingBox(); const headerBox = await page.locator('header').first().boundingBox();
  expect(inputBox!.y).toBeGreaterThanOrEqual(headerBox!.y + headerBox!.height);
  await cap.fill('0.10');
  await expect(page.getByText(/This cap may cover only part/)).toBeVisible();
  await consent(page).check();
  await expect(page.getByRole('button', { name: 'Convert the sample', exact: true })).toBeEnabled();
  await cap.fill('0.20'); await expect(consent(page)).not.toBeChecked();
  await consent(page).check();
  await page.getByRole('radio', { name: /^The whole book/ }).check();
  await expect(consent(page)).not.toBeChecked();
  await expect(card.getByText('$0.36', { exact: true })).toBeVisible();
  await consent(page).check();
  await page.getByRole('button', { name: 'Convert the book', exact: true }).click();
  await expect.poll(() => state.starts.length).toBe(1);
  expect(state.starts[0]).toMatchObject({ review_mode: 'source_verified', cost_cap_usd: .2,
    preparation_id: 'd'.repeat(32), consent_contract: 'source-review-1' });
  assertNoPageErrors(errors);
});

test('local preparation is cancellable and changed recovery invalidates ready consent', async ({ page }) => {
  const est = assessment(); est.recovery.ocr_candidates = 1; est.recovery.image_only = 1;
  const { state, errors } = await stub(page, est); state.status = 'preparing';
  await paidChoice(page).check(); await page.getByRole('button', { name: 'Prepare AI estimate', exact: true }).click();
  await expect(page.getByText('Preparing source context: 8 of 60', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Stop preparation', exact: true }).click();
  await expect(page.getByText('Source preparation stopped. No model request was sent.', { exact: true })).toBeVisible();
  await expect(consent(page)).toBeDisabled();
  state.status = 'ready'; await page.getByRole('button', { name: 'Prepare AI estimate', exact: true }).click();
  await expect(consent(page)).toBeEnabled(); await consent(page).check();
  await page.getByRole('textbox', { name: /Recognition language/ }).fill('fra');
  await expect(consent(page)).not.toBeChecked(); await expect(consent(page)).toBeDisabled();
  expect(state.starts).toHaveLength(0); expect(state.cancellations).toBeGreaterThan(0);
  assertNoPageErrors(errors);
});

test('actual typed counts, microcharges, held costs and file identity stay separate', async ({ page }) => {
  const { errors } = await stub(page, assessment(), [reviewed]);
  const result = page.locator('section[aria-labelledby="reflow-result"]');
  await expect(result.getByText('$0.0022', { exact: true })).toBeVisible();
  await expect(result.getByText('$0.0217', { exact: true })).toBeVisible();
  await expect(result).toContainText('Approved operations');
  await expect(result).toContainText('Cached stage results');
  await expect(result).toContainText('not independent proof of correctness');
  await expect(result).toContainText('c'.repeat(64));
  await expect(result.getByRole('link', { name: 'Download the sample' })).toBeVisible();
  await preparePaid(page);
  const held = page.getByRole('checkbox', { name: /^I understand the unconfirmed/ });
  await consent(page).check(); await expect(page.getByRole('button', { name: 'Convert the sample', exact: true })).toBeDisabled();
  await held.check(); await expect(page.getByRole('button', { name: 'Convert the sample', exact: true })).toBeEnabled();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(async (value) => {
      document.documentElement.setAttribute('data-theme', value); window.scrollTo(0, 0);
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    }, theme);
    await assertNoHorizontalOverflow(page);
    const findings = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(findings.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
    await page.screenshot({ path: test.info().outputPath(`typed-review-${theme}.jpg`), type: 'jpeg', quality: 75, fullPage: true });
  }
  await page.reload(); await paidChoice(page).check();
  await expect(held).not.toBeChecked();
  assertNoPageErrors(errors);
});

test('invalid cap is associated with its field and a failed preparation buys nothing', async ({ page }) => {
  const { state, errors } = await stub(page); state.status = 'failed';
  await paidChoice(page).check(); await page.getByRole('button', { name: 'Prepare AI estimate', exact: true }).click();
  await expect(page.getByText('Source estimate preparation failed. No model request was sent.', { exact: true })).toBeVisible();
  await expect(consent(page)).toBeDisabled();
  const cap = page.getByRole('spinbutton', { name: /Stop after spending/ });
  await cap.fill('6'); await expect(cap).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('#reflow-cap-error')).toContainText('positive cap');
  expect(state.starts).toHaveLength(0); assertNoPageErrors(errors);
});
