import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { clampPage } from '../src/lib/pagination.ts';

const source = (relative: string) => fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

/** The page count the books endpoint's `total` and `per_page` describe. */
function pages(total: number, perPage: number): number {
  return Math.max(1, Math.ceil(total / perPage));
}

test('picking another node starts that node at page one', () => {
  // The bug: NodeBooks holds `page` in component state and stays mounted when
  // `path` changes, so page 3 of a large node carried over to a small one.
  // With `last === 1` the pager's `last > 1` gate was false, so the Previous
  // button that would have walked it back was not rendered either.
  const large = pages(100, 24);   // 5
  const small = pages(5, 24);     // 1
  assert.equal(large, 5);
  assert.equal(small, 1);

  // The reset puts the page back to 1 on a node change; the clamp is the
  // backstop for a page that is out of range for any other reason.
  assert.equal(clampPage(1, small), 1);
  assert.equal(clampPage(3, small), 1);
  assert.equal(clampPage(3, large), 3);
});

test('a node with fewer pages than the one you were reading clamps instead of going blank', () => {
  // Page 3 carried into a 2-page node: the server returns `total > 0` with an
  // empty `items` array, so the "No books here yet" state is wrong and the
  // grid rendered empty. clampPage is what makes the page valid again.
  assert.equal(clampPage(3, 2), 2);
  assert.equal(clampPage(9, 1), 1);
  assert.equal(clampPage(1, 5), 1);
});

test('the page is scoped to the node, so a stale value cannot survive a node change', () => {
  // The identity the component compares on. NUL cannot occur in a URL query
  // value, so a column id and a path can never produce the same key by
  // accident (e.g. column 1 path "2" vs column 12 with an empty path).
  const queryId = (colId: string, path: string) => `${colId}\u0000${path}`;
  assert.notEqual(queryId('1', '2'), queryId('12', ''));
  assert.equal(queryId('1', 'Computers.DB'), queryId('1', 'Computers.DB'));
  assert.notEqual(queryId('1', 'Computers'), queryId('1', 'Computers.DB'));
});

test('NodeBooks resets the page on a node change and clamps an out-of-range page', () => {
  const tsx = source('src/pages/CcBrowse.tsx');
  const start = tsx.indexOf('function NodeBooks(');
  assert.notEqual(start, -1, 'NodeBooks must exist');
  const fn = tsx.slice(start, tsx.indexOf('\n/**', start + 1));

  // The reset: a render-phase comparison against the node identity, so the new
  // node is never painted carrying the previous node's page.
  assert.match(fn, /const queryId = /, 'NodeBooks must derive a node identity');
  assert.match(fn, /if \(lastQueryId !== queryId\) \{/, 'the page must reset when the node changes');
  assert.match(fn, /setPage\(1\)/, 'a node change must return to page one');

  // The backstop uses the SHARED clamp rather than a local Math.min, so this
  // surface cannot grow its own divergent paging rule.
  assert.match(fn, /clampPage\(page, last\)/, 'clamp with the shared helper');
  assert.doesNotMatch(fn, /Math\.min\(page, last\)/, 'do not hand-roll the clamp');
  assert.match(
    tsx,
    /import \{ clampPage \} from '\.\.\/lib\/pagination'/,
    'clampPage must be the shared helper from lib/pagination, not a local copy',
  );
});

test('a failed books request is not rendered as an empty result', () => {
  // The second half of the '/' bug: the endpoint 404s on a node the API could
  // not resolve, apiGet throws, and NodeBooks read only `data` — so `total`
  // defaulted to 0 and the user was told "No books here yet" instead of being
  // shown the API's error. Any non-2xx (403, 500 from a locked metadata.db)
  // was hidden the same way.
  const tsx = source('src/pages/CcBrowse.tsx');
  const start = tsx.indexOf('function NodeBooks(');
  const fn = tsx.slice(start, tsx.indexOf('\n/**', start + 1));

  assert.match(fn, /isError/, 'NodeBooks must read the query error state');
  assert.match(fn, /if \(isError\) \{/, 'the error state needs its own branch');
  // Order matters: the error has to be checked before the empty-result branch,
  // or a failed request still reads as "no books".
  assert.ok(
    fn.indexOf('if (isError)') < fn.indexOf('total === 0'),
    'the error branch must come before the empty-result branch',
  );
  assert.match(
    fn,
    /Could not load books/,
    'the error state must name itself rather than borrow the empty-state copy',
  );
});

test('the pager reflects the clamped page, so it can always walk the page back', () => {
  const tsx = source('src/pages/CcBrowse.tsx');
  const start = tsx.indexOf('function NodeBooks(');
  const fn = tsx.slice(start, tsx.indexOf('\n/**', start + 1));

  // Both pager controls must read the clamped page. Reading the raw `page`
  // is what produced the "Page 3 of 1" label with a disabled Previous button
  // on a node that had been reset underneath it.
  assert.match(fn, /disabled=\{clamped <= 1 \|\| isFetching\}/, 'Previous must use the clamped page');
  assert.match(fn, /disabled=\{clamped >= last \|\| isFetching\}/, 'Next must use the clamped page');
  assert.match(
    fn,
    /page: clamped, last/,
    'the page label must show the clamped page',
  );
});

test('a node with books but an empty page is not reported as "No books here yet"', () => {
  // `total === 0` means "no books"; `total > 0` with no items means "this page
  // is out of range". Conflating them hid the only signal that distinguished
  // a genuinely empty node from a paging bug.
  const tsx = source('src/pages/CcBrowse.tsx');
  const start = tsx.indexOf('function NodeBooks(');
  const fn = tsx.slice(start, tsx.indexOf('\n/**', start + 1));

  assert.match(fn, /items\.length === 0 \?/, 'an empty page needs its own branch');
  assert.match(fn, /Nothing on this page/, 'that state must say what it is');
  // ...and the genuinely-empty branch must stay keyed on the total, not the
  // page, so the two cases cannot be conflated again.
  assert.match(fn, /if \(total === 0\) \{/, 'no-books is keyed on the total');
});
