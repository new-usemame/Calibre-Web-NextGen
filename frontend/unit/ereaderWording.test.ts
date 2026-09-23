import assert from 'node:assert/strict';
import test from 'node:test';

import { shelfMarkAudience, shelfMarksReachDevices } from '../src/lib/ereaderWording.ts';

test('a shelf mark reaches devices when either Kobo sync or KOReader sync is on', () => {
  assert.equal(shelfMarksReachDevices({ kobo_sync: true, koreader_sync: false }), true);
  assert.equal(shelfMarksReachDevices({ kobo_sync: false, koreader_sync: true }), true);
  assert.equal(shelfMarksReachDevices({ kobo_sync: false, koreader_sync: false }), false);
  // An older server sends no koreader_sync at all.
  assert.equal(shelfMarksReachDevices({ kobo_sync: false }), false);
  assert.equal(shelfMarksReachDevices(undefined), false);
});

test('the wording names Kobo until KOReader can receive shelves too', () => {
  assert.equal(shelfMarkAudience({ kobo_sync: true }), 'kobo');
  assert.equal(shelfMarkAudience({ kobo_sync: true, koreader_sync: false }), 'kobo');
  assert.equal(shelfMarkAudience({ kobo_sync: true, koreader_sync: true }), 'ereader');
  assert.equal(shelfMarkAudience({ kobo_sync: false, koreader_sync: true }), 'ereader');
  assert.equal(shelfMarkAudience(null), 'kobo');
});
