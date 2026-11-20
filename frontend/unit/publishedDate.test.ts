import assert from 'node:assert/strict';
import test from 'node:test';

import { formatPublishedDate } from '../src/lib/customColumnDisplay.ts';

// #2634: a publication date is a calendar day. Read as UTC midnight and shown
// in a timezone west of UTC, it rendered as the day before. The suite runs the
// same expectations in a western, an eastern and the UTC zone.
const zones = ['America/Los_Angeles', 'Pacific/Kiritimati', 'UTC'];

for (const zone of zones) {
  test(`pubdate keeps its calendar day in ${zone}`, () => {
    const previous = process.env.TZ;
    process.env.TZ = zone;
    try {
      assert.equal(formatPublishedDate('2021-03-15', 'en'), 'March 15, 2021');
      assert.equal(formatPublishedDate('1999-12-31', 'en'), 'December 31, 1999');
      // A year-only date (stored as 1 January) shows just the year, in every zone.
      assert.equal(formatPublishedDate('2020-01-01', 'en'), '2020');
    } finally {
      if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous;
    }
  });
}

test('pubdate uses the reader locale and leaves unparseable values alone', () => {
  assert.equal(formatPublishedDate('2021-03-15', 'de'), '15. März 2021');
  assert.equal(formatPublishedDate('not a date', 'en'), 'not a date');
});
