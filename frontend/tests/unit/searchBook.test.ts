/*
 * Tests for search-inside-the-book.
 *
 * Node's built-in runner and native type stripping, matching reportBuilder's
 * choice: the frontend ships no test framework on purpose, because adding one is
 * a new dependency and operator-gated. These now actually run in CI, via
 * tests/unit/test_frontend_unit_suites_run.py.
 *
 * Run: NODE_OPTIONS=--experimental-strip-types node --test frontend/tests/unit/searchBook.test.ts
 *
 * The module takes the book's shape rather than importing epub.js, so every case
 * below uses a fake spine. That is what makes the properties that matter here —
 * unloading, cancelling, capping — testable at all: none of them are observable
 * from the return value.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  searchBook, MIN_QUERY_LENGTH, DEFAULT_HIT_CAP,
  type SearchableBook, type SearchableSection,
} from '../../src/lib/reader/searchBook.ts';

/** A section that records whether it was loaded and unloaded. */
function fakeSection(
  href: string,
  matches: { cfi: string; excerpt: string }[],
  opts: { throwOnLoad?: boolean; throwOnSearch?: boolean } = {},
) {
  const state = { loaded: 0, unloaded: 0, searched: 0 };
  const section: SearchableSection & { state: typeof state } = {
    href,
    state,
    load: async () => {
      state.loaded += 1;
      if (opts.throwOnLoad) throw new Error(`cannot load ${href}`);
      return undefined;
    },
    unload: () => { state.unloaded += 1; },
    search: () => {
      state.searched += 1;
      if (opts.throwOnSearch) throw new Error(`cannot search ${href}`);
      return matches;
    },
  };
  return section;
}

function fakeBook(sections: SearchableSection[]): SearchableBook {
  return { spine: { spineItems: sections }, load: { bind: () => undefined } };
}

const hit = (n: string) => ({ cfi: `epubcfi(/6/${n})`, excerpt: `…${n}…` });

describe('searchBook', () => {
  test('returns a hit per match, tagged with the section it came from', async () => {
    const book = fakeBook([
      fakeSection('ch1.xhtml', [hit('a'), hit('b')]),
      fakeSection('ch2.xhtml', [hit('c')]),
    ]);
    const out = await searchBook(book, 'whale');
    assert.equal(out.hits.length, 3);
    assert.deepEqual(out.hits.map((h) => h.href), ['ch1.xhtml', 'ch1.xhtml', 'ch2.xhtml']);
    assert.equal(out.sectionsSearched, 2);
    assert.equal(out.truncated, false);
  });

  test('unloads every section it loads', async () => {
    // The property that keeps a long book from exhausting memory, and it is
    // invisible in the return value — only the fake can see it.
    const sections = [fakeSection('a', [hit('1')]), fakeSection('b', []), fakeSection('c', [hit('2')])];
    await searchBook(fakeBook(sections), 'whale');
    for (const s of sections as (SearchableSection & { state: { loaded: number; unloaded: number } })[]) {
      assert.equal(s.state.loaded, 1, `${s.href} loaded once`);
      assert.equal(s.state.unloaded, 1, `${s.href} unloaded once`);
    }
  });

  test('unloads a section even when searching it throws', async () => {
    // The error path is exactly where an unload gets skipped, and a book with a
    // few bad chapters is the case that would then hold their DOMs.
    const bad = fakeSection('bad', [], { throwOnSearch: true });
    const good = fakeSection('good', [hit('1')]);
    const out = await searchBook(fakeBook([bad, good]), 'whale');
    assert.equal((bad as unknown as { state: { unloaded: number } }).state.unloaded, 1);
    assert.equal(out.hits.length, 1, 'the good section still contributes');
  });

  test('a section that will not load costs only its own results', async () => {
    const broken = fakeSection('broken', [hit('x')], { throwOnLoad: true });
    const fine = fakeSection('fine', [hit('y')]);
    const out = await searchBook(fakeBook([broken, fine]), 'whale');
    assert.equal(out.hits.length, 1);
    assert.equal(out.hits[0].href, 'fine');
    assert.equal((broken as unknown as { state: { unloaded: number } }).state.unloaded, 1);
  });

  test('stops at the cap and says that it did', async () => {
    const many = Array.from({ length: 10 }, (_, i) =>
      fakeSection(`s${i}`, [hit('1'), hit('2'), hit('3')]));
    const out = await searchBook(fakeBook(many), 'the', { cap: 5 });
    assert.equal(out.hits.length, 5);
    assert.equal(out.truncated, true, 'the UI must be able to say these are not all the matches');
    // ...and it stopped early rather than searching the whole book and slicing.
    assert.ok(out.sectionsSearched < 10, `searched ${out.sectionsSearched} of 10`);
  });

  test('an exact-cap result is not reported as truncated', async () => {
    // Off-by-one guard: exactly `cap` hits with nothing left over is complete,
    // and claiming otherwise tells the reader to refine a search that was fine.
    // 'x' would be below MIN_QUERY_LENGTH and search nothing -- which is what
    // this asserted on the first run, and it was the test that was wrong.
    const out = await searchBook(fakeBook([fakeSection('only', [hit('1'), hit('2')])]), 'whale', { cap: 2 });
    assert.equal(out.hits.length, 2);
    assert.equal(out.truncated, false);
  });

  test('unloads the section it was in when the cap stopped it mid-way', async () => {
    /*
     * Pins the observable property: a section the cap interrupted is still
     * unloaded.
     *
     * Honest about what this does NOT prove. Moving the unload out of `finally`
     * passes every test in this file, including this one, so the `finally` is
     * defensive rather than load-bearing today: the catch swallows the throw and
     * execution reaches the unload anyway, the inner cap `break` only leaves the
     * inner loop, and the outer breaks happen before anything is loaded. It is
     * kept because the next `return` or `throw` added inside that block would
     * make it matter, and a leaked section DOM is silent when it happens.
     */
    const first = fakeSection('first', [hit('1'), hit('2'), hit('3')]);
    const out = await searchBook(fakeBook([first, fakeSection('second', [hit('4')])]), 'whale', { cap: 2 });
    assert.equal(out.hits.length, 2);
    assert.equal(out.truncated, true);
    assert.equal((first as unknown as { state: { unloaded: number } }).state.unloaded, 1,
      'the section the cap interrupted must still be unloaded');
  });

  test('an aborted search stops instead of finishing the book', async () => {
    const controller = new AbortController();
    const sections = Array.from({ length: 20 }, (_, i) => fakeSection(`s${i}`, [hit('1')]));
    // Abort as soon as the first section has been searched.
    const original = sections[0].search!;
    sections[0].search = (q: string) => { const r = original(q); controller.abort(); return r; };

    const out = await searchBook(fakeBook(sections), 'whale', { signal: controller.signal });
    assert.ok(out.sectionsSearched < 20, `searched ${out.sectionsSearched} of 20 after abort`);
    const untouched = (sections[19] as unknown as { state: { loaded: number } }).state.loaded;
    assert.equal(untouched, 0, 'sections after the abort are never loaded');
  });

  test('a query shorter than the minimum searches nothing at all', async () => {
    const s = fakeSection('a', [hit('1')]);
    for (const q of ['', ' ', 'x'.repeat(MIN_QUERY_LENGTH - 1)]) {
      const out = await searchBook(fakeBook([s]), q);
      assert.equal(out.hits.length, 0, `${JSON.stringify(q)} should not search`);
    }
    assert.equal((s as unknown as { state: { loaded: number } }).state.loaded, 0,
      'a too-short query must not load a single section');
  });

  test('whitespace around a query does not change what is searched', async () => {
    let seen = '';
    const s: SearchableSection = {
      href: 'a', load: async () => undefined, unload: () => {},
      search: (q) => { seen = q; return []; },
    };
    await searchBook(fakeBook([s]), '  whale  ');
    assert.equal(seen, 'whale');
  });

  test('falls back to find() where search() is unavailable', async () => {
    // epub.js itself degrades this way when there is no TreeWalker; the module
    // must not simply return nothing on that path.
    const s: SearchableSection = {
      href: 'a', load: async () => undefined, unload: () => {},
      find: () => [hit('1')],
    };
    const out = await searchBook(fakeBook([s]), 'whale');
    assert.equal(out.hits.length, 1);
  });

  test('an empty or missing spine is not an error', async () => {
    assert.equal((await searchBook(fakeBook([]), 'whale')).hits.length, 0);
    const noSpine = { load: { bind: () => undefined } } as unknown as SearchableBook;
    assert.equal((await searchBook(noSpine, 'whale')).hits.length, 0);
  });

  test('the default cap is a real number, not Infinity', async () => {
    // A cap nobody set is the case that would let "the" return every match in a
    // long book, which is the scenario this exists for.
    assert.ok(Number.isFinite(DEFAULT_HIT_CAP) && DEFAULT_HIT_CAP > 0, String(DEFAULT_HIT_CAP));
  });
});
