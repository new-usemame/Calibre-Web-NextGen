import assert from 'node:assert/strict';
import test from 'node:test';
import { selectedReview, preparationActive, type ReviewQuote } from '../src/lib/sourceReview.ts';
import { holdRequiringAcknowledgment } from '../src/lib/reflowMoney.ts';

test('selected quote prices actual body page IDs, not proportional routed estimates', () => {
  const quote = { source_context_pages: 90, first_body_page: 5,
    pages: [0, 5, 7, 40].map((page_index0) => ({ page_index0, proposer_bound_usd: .02, verifier_bound_usd: .1 })),
    coverage: [{ page: 6, status: 'unsupported' }, { page: 8, status: 'limited' }, { page: 9, status: 'no_choices' }],
  } as ReviewQuote;
  const sample = selectedReview(quote, 'sample', 5);
  assert.deepEqual(sample, { start: 5, end: 10, eligible: 2, bound: .24000000000000002,
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
