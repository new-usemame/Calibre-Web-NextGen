import assert from 'node:assert/strict';
import test from 'node:test';
import { selectedReview, preparationActive, preparationNote, type ReviewQuote, type ReviewPreparation } from '../src/lib/sourceReview.ts';
import { holdRequiringAcknowledgment } from '../src/lib/reflowMoney.ts';

test('selected quote prices actual body page IDs, not proportional routed estimates', () => {
  const quote = { source_context_pages: 90, first_body_page: 5,
    pages: [0, 5, 7, 40].map((page_index0) => ({ page_index0, proposer_bound_usd: .02, verifier_bound_usd: .1 })),
    coverage: [{ page: 6, status: 'unsupported' }, { page: 8, status: 'limited' }, { page: 9, status: 'no_choices' }],
  } as ReviewQuote;
  const sample = selectedReview(quote, 'sample', 5);
  assert.deepEqual(sample, { start: 5, end: 10, eligible: 2, primary: .04, retry: .2, markers: 0, expected: 0, target: .5, bound: .24000000000000002,
    unsupported: 1, limited: 1, noChoices: 1 });
  assert.equal(selectedReview(quote, 'full', 5).eligible, 4);
  assert.equal(selectedReview(undefined, 'sample', 5).bound, 0);
});

test('preparation polling ends at every terminal state', () => {
  for (const state of ['waiting', 'preparing', 'cancelling'] as const) assert.ok(preparationActive(state));
  for (const state of ['ready', 'failed', 'cancelled', undefined] as const) assert.equal(preparationActive(state), false);
});

test('a new paid consent names all still-unresolved job liabilities', () => {
  assert.equal(holdRequiringAcknowledgment([{ pending_usd: .1 }, { pending_usd: 0 }, { pending_usd: .2 }]), .1 + .2);
});

test('a preparation waiting its turn says how many are ahead, and a timed-out one says it can continue', () => {
  const prep = (fields: Partial<ReviewPreparation>) => ({ preparation_id: 'p', status: 'waiting', progress: {}, ...fields }) as ReviewPreparation;
  assert.deepEqual(preparationNote(prep({ status: 'waiting', ahead: 2 }), false, false, false), { kind: 'queued', ahead: 2 });
  assert.deepEqual(preparationNote(prep({ status: 'waiting', ahead: 0 }), false, false, false), { kind: 'preparing', page: 0, pages: undefined });
  assert.deepEqual(preparationNote(prep({ status: 'preparing', progress: { page: 3, pages: 9 } }), false, false, false),
    { kind: 'preparing', page: 3, pages: 9 });
  assert.deepEqual(preparationNote(prep({ status: 'failed', error: 'source_preparation_timeout', timeout_minutes: 30 }), false, false, false),
    { kind: 'timed_out', minutes: 30 });
  assert.deepEqual(preparationNote(prep({ status: 'failed', error: 'source_preparation_failed' }), false, false, false), { kind: 'failed' });
  assert.deepEqual(preparationNote(prep({ status: 'cancelled' }), false, false, false), { kind: 'cancelled' });
  assert.deepEqual(preparationNote(prep({ status: 'ready' }), false, false, true), { kind: 'ready' });
  assert.deepEqual(preparationNote(undefined, true, false, false), { kind: 'preparing', page: 0, pages: undefined });
  assert.deepEqual(preparationNote(undefined, false, true, false), { kind: 'failed' });
  assert.deepEqual(preparationNote(undefined, false, false, false), { kind: 'none' });
});

test('layout consent includes ordering and lexical ceilings for the selected pages', () => {
  const quote = { source_context_pages: 40, first_body_page: 5,
    pages: [{ page_index0: 5, proposer_bound_usd: .02, verifier_bound_usd: .03, batch_bound_usd: .2, full_bound_usd: .25 },
      { page_index0: 20, proposer_bound_usd: .02, verifier_bound_usd: .03, batch_bound_usd: .4, full_bound_usd: .45 }],
    coverage: [] } as unknown as ReviewQuote;
  assert.equal(selectedReview(quote, 'sample', 5).bound, .25);
  assert.equal(selectedReview(quote, 'full', 5).bound, .7);
});

test('vision consent sums primary, retry and marker ceilings for only selected PDF pages', () => {
  const quote = { source_context_pages: 40, first_body_page: 0, target_usd: .5,
    pages: [{ page_index0: 0, primary_bound_usd: .02, retry_bound_usd: .03,
      marker_bound_usd: .01, expected_usd: .004, full_bound_usd: .06 },
      { page_index0: 20, primary_bound_usd: .04, retry_bound_usd: .05,
        marker_bound_usd: .02, expected_usd: .008, full_bound_usd: .11 }], coverage: [] } as unknown as ReviewQuote;
  const sample = selectedReview(quote, 'sample', 5);
  assert.equal(sample.primary, .02);
  assert.equal(sample.retry, .03);
  assert.equal(sample.markers, .01);
  assert.equal(sample.expected, .004);
  assert.equal(sample.target, .5);
  assert.equal(sample.bound, .06);
  const full = selectedReview(quote, 'full', 5);
  assert.equal(full.primary, .06);
  assert.equal(full.retry, .08);
  assert.equal(full.markers, .03);
  assert.equal(full.expected, .012);
  assert.equal(full.bound, .06 + .11);
});

test('a vision sample uses the quote start bound to its selected page count', () => {
  const quote = { source_context_pages: 80, first_body_page: 20, sample_starts: { '5': 2, '10': 7 },
    pages: [2, 7, 20].map((page_index0) => ({ page_index0, full_bound_usd: .02 })), coverage: [] } as unknown as ReviewQuote;
  assert.equal(selectedReview(quote, 'sample', 5).start, 2);
  assert.equal(selectedReview(quote, 'sample', 5).bound, .02);
  assert.equal(selectedReview(quote, 'sample', 10).start, 7);
  assert.equal(selectedReview(quote, 'sample', 10).bound, .02);
  assert.equal(selectedReview(quote, 'full', 10).start, 0);
  assert.equal(selectedReview(quote, 'full', 10).bound, .06);
});
