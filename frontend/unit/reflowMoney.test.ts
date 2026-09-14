/* The arithmetic on the Reflow page is the only thing standing between a reader and
 * a charge they did not agree to, and it is a second implementation of a sum the
 * server also does. `cps/api/reflow.py` refuses a start whose cap is below its own
 * figure, so a page that quotes a number the server will not accept is a page
 * nobody can get past; a page that quotes less than the server will charge is
 * worse. These tests hold the two together at the places where they can drift. */
import assert from 'node:assert/strict';
import test from 'node:test';

import type { ReflowEstimate } from '../src/lib/reflowMoney.ts';
import {
  requiredUsd, sampleRoutedPages, suggestedCap, usd,
} from '../src/lib/reflowMoney.ts';

/** Per page, at the ceilings `cps/services/reflow/model.py` prices a tier by. */
const PRICE: Record<string, number> = {
  cheap: 0.001094, standard: 0.00294, quality: 0.00286,
};
const TIERS = Object.keys(PRICE);

/** A payload shaped as `cps/api/reflow.py::_estimate_payload` builds one.
 *
 *  `estimate_usd` is derived here rather than invented, because that is how
 *  `route.estimate()` derives it: the tier's price per page times the routed count.
 *  A fixture carrying made-up totals would let the page and the server disagree
 *  about a book without any of this noticing. */
function estimateFor(pages: number, routed: number, hardCap = 5): ReflowEstimate {
  const money = (tier: string, count: number) =>
    Math.round(PRICE[tier] * count * 10000) / 10000;
  return {
    book_id: 224,
    title: 'a book somebody is about to pay to convert',
    verdict: 'needs_model',
    pages,
    text_layer: true,
    routed_pages_estimate: routed,
    routed_share: routed / pages,
    estimate_usd: Object.fromEntries(TIERS.map((t) => [t, money(t, routed)])),
    worst_case_usd: Object.fromEntries(TIERS.map((t) => [t, money(t, pages)])),
    target_usd: 0.5,
    over_target: money('standard', routed) > 0.5,
    sample_suggested: true,
    existing_epub: false,
    configured: true,
    default_tier: 'standard',
    hard_cap_usd: hardCap,
    sample_pages_default: 20,
    sample_pages_max: 60,
    tiers: TIERS.map((tier) => ({
      tier, model: `provider/${tier}`, label: tier, price_per_page: PRICE[tier],
    })),
    priced_on: '2026-09-13',
    sampled: 12,
    reasons: { footnotes: routed },
    cached: false,
  };
}

/** Books of the shapes the acceptance run met: a 698-page one that routes about
 *  half its pages, a short one a sample can cover entirely, and a single page. */
const BOOKS = [
  estimateFor(698, 330), estimateFor(40, 25), estimateFor(1, 1),
  estimateFor(250, 0),
];

test('the cap the page fills in is one the server will start on', () => {
  for (const est of BOOKS) {
    for (const tier of TIERS) {
      for (const mode of ['sample', 'full'] as const) {
        for (const sample of [1, 5, 20, 60]) {
          const needed = requiredUsd(est, tier, mode, sample);
          const cap = suggestedCap(needed, est.hard_cap_usd);
          const where = `${est.pages}pp ${tier} ${mode} ${sample}`;
          assert.ok(cap <= est.hard_cap_usd + 1e-9,
                    `${where}: suggested ${cap} is over the administrator's ceiling`);
          // The server's own comparison, cps/api/reflow.py::reflow_start.
          assert.ok(cap + 1e-9 >= needed,
                    `${where}: suggested ${cap} is under the quoted ${needed}`);
        }
      }
    }
  }
});

test('the suggested cap leaves a book room to run dearer than its estimate', () => {
  // The estimate is an estimate: it is priced off a sample of the book's pages, and
  // a book whose later chapters carry more footnotes than its sample did will spend
  // more than the figure. A cap set exactly at the estimate turns that ordinary
  // overrun into a conversion that stops at 98% and keeps a truncated EPUB.
  for (const estimate of [0.02, 0.36, 0.97, 2.5]) {
    const cap = suggestedCap(estimate, 20);
    assert.ok(cap >= estimate * 1.1,
              `a $${estimate} book was capped at $${cap}, which a 10% overrun eats`);
  }
});

test('a sample is priced at the book\'s own routing rate, not page for page', () => {
  // Half of this book reaches a model; the other half converts for nothing. Pricing
  // twenty sample pages as twenty model calls asks the reader to authorise twice
  // what the sample can possibly cost -- and twice what the server will charge,
  // which computes the same share in cps/api/reflow.py::_sample_share.
  const est = estimateFor(698, 330);
  const rate = est.routed_pages_estimate / est.pages;
  for (const sample of [10, 20, 40, 60]) {
    const routed = sampleRoutedPages(est, sample);
    assert.ok(Math.abs(routed - sample * rate) <= 1,
              `a ${sample}-page sample was priced at ${routed} model pages, `
              + `where this book routes ${(rate * 100).toFixed(0)}% of its pages`);
  }
});

test('a cap is never suggested as nothing, because nothing means the whole ceiling',
     () => {
       // cps/tasks/reflow.py::_clamp_cap reads a cap of zero as "no cap given" and
       // hands the job the administrator's entire ceiling. A page that pre-filled 0
       // would take a reader who was shown $0.00 and authorise five dollars.
       for (const hardCap of [0.5, 5, 20]) {
         assert.ok(suggestedCap(0, hardCap) > 0,
                   `a free-looking book suggested ${suggestedCap(0, hardCap)}`);
       }
     });

test('sampling every page of a book is quoted as converting the book', () => {
  const est = estimateFor(40, 25);
  assert.equal(sampleRoutedPages(est, est.pages), est.routed_pages_estimate);
  for (const tier of TIERS) {
    assert.equal(requiredUsd(est, tier, 'sample', est.pages),
                 requiredUsd(est, tier, 'full', est.sample_pages_default), tier);
  }
});

test('a sample of a long book is never quoted at nothing, nor above the whole book',
     () => {
       const est = estimateFor(698, 330);
       for (const tier of TIERS) {
         const whole = requiredUsd(est, tier, 'full', est.sample_pages_default);
         for (let sample = 1; sample <= est.sample_pages_max; sample += 1) {
           const quoted = requiredUsd(est, tier, 'sample', sample);
           assert.ok(quoted > 0,
                     `${tier}: a ${sample}-page sample was quoted at ${quoted}`);
           // Strictly less: sixty pages of a six-hundred-and-ninety-eight-page book
           // is a part of it, and a sample priced as the whole book is the mistake
           // _sample_share exists to prevent.
           assert.ok(quoted < whole,
                     `${tier}: a ${sample}-page sample cost ${quoted}, all 698 cost `
                     + `${whole}`);
         }
       }
     });

test('the quoted figure is never rounded down below what will be charged', () => {
  // 1.005 is not 1.005 in binary; it is a shade under, and Math.round alone answers
  // $1.00 to a charge of $1.005. Every figure on this page is a promise about money.
  assert.equal(usd(1.005), '$1.01');
  assert.equal(usd(0.015), '$0.02');
  for (const amount of [0, 0.0001, 0.004, 0.97, 1.005, 4.999, 12]) {
    const shown = usd(amount);
    assert.match(shown, /^\$\d+\.\d{2}$/, `${amount} was shown as ${shown}`);
    assert.ok(Number(shown.slice(1)) + 0.005 + 1e-9 >= amount,
              `${amount} was shown as ${shown}`);
  }
});
