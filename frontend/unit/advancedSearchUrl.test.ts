import assert from 'node:assert/strict';
import test from 'node:test';
import { advancedSearchFromQuery, advancedSearchToQuery } from '../src/lib/advancedSearchUrl.ts';

/* #2211: the submitted advanced search is carried in the URL so it survives
 * opening a result and coming back, and a reload. The URL is now the only
 * record of the query, so a value that does not survive the round trip is a
 * search the user silently loses or silently changes. */
test('a query survives the URL round trip, including values containing URL syntax', () => {
  const params = {
    title: 'Pride & Prejudice = 1+1',
    authors: 'Brontë',
    comments: 'dragons?#',
    read_status: 'unread' as const,
    publishstart: '1990-01-01',
    rating_low: '3',
    include_tag: [3, 17],
    exclude_tag: [9],
    include_serie: [4],
    include_language: [2],
    include_extension: ['epub', 'pdf'],
    exclude_extension: ['cbz'],
  };
  assert.deepEqual(advancedSearchFromQuery(advancedSearchToQuery(params)), params);
});

test('an empty search writes no query, and a bare or foreign URL restores no search', () => {
  const empty = {
    title: '', authors: '', publisher: '', comments: '', read_status: 'all' as const,
    publishstart: '', publishend: '', rating_low: '', rating_high: '',
    include_tag: [], exclude_tag: [], include_extension: [],
  };
  assert.equal(advancedSearchToQuery(empty), '');
  assert.equal(advancedSearchFromQuery(''), null);
  assert.equal(advancedSearchFromQuery('?utm_source=x&page=2'), null);
});

test('values the form cannot show are dropped instead of being searched for', () => {
  assert.deepEqual(
    advancedSearchFromQuery('read_status=maybe&rating_low=9&publishend=yesterday&title=%20dune%20'),
    { title: 'dune' },
  );
});

/* #2365: custom-column criteria ride the same URL, or a custom-column search
 * is lost on the first back-navigation or reload. */
test('custom-column criteria survive the URL round trip on their own', () => {
  const params = {
    custom: {
      custom_column_4: 'Cover & spine',
      custom_column_2_low: '100',
      custom_column_3_end: '2026-09-01',
      custom_column_1: 'Empty',
    },
  };
  assert.deepEqual(advancedSearchFromQuery(advancedSearchToQuery(params)), params);
});

test('blank custom fields and foreign custom-looking keys are not carried', () => {
  assert.equal(advancedSearchToQuery({ custom: { custom_column_4: ' ', custom_column_x: 'a' } }), '');
  assert.equal(advancedSearchFromQuery('custom_column_2_median=5&custom_column_=1'), null);
});

/* "Edit default view" must open the form on the saved criteria, custom
 * columns included; it linked to a bare /search, an empty form. */
test('the edit link for a saved default view carries its criteria', async () => {
  const { advancedSearchHref } = await import('../src/lib/advancedSearchUrl.ts');
  const saved = { include_tag: [3], read_status: 'unread' as const, custom: { custom_column_1: 'dark' } };
  const href = advancedSearchHref(saved);
  assert.equal(href.split('?')[0], '/search');
  assert.deepEqual(advancedSearchFromQuery(href.slice(href.indexOf('?'))), saved);
  assert.equal(advancedSearchHref(null), '/search');
});


test('exact reading statuses survive a bookmarked search and a remount', () => {
  for (const status of ['in_progress', 'did_not_finish', 'on_hold'] as const) {
    const params = { title: 'Book', read_status: status };
    const query = advancedSearchToQuery(params);
    assert.deepEqual(advancedSearchFromQuery(query), params);
    assert.equal(new URLSearchParams(query).get('read_status'), status);
  }
});
