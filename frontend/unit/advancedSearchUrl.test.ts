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
