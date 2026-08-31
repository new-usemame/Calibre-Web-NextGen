import assert from 'node:assert/strict';
import test from 'node:test';

import {
  koboConfigLine, latestPairingDevice, pairingVisibility,
} from '../src/lib/koboPairing.ts';
import { relativeWhen } from '../src/lib/relativeTime.ts';

test('stock Kobo config uses the server-provided token URL verbatim', () => {
  const url = 'https://books.example.test/kobo/0123456789abcdef';
  assert.equal(koboConfigLine(url), `api_endpoint=${url}`);
});

test('device-seen confirmation chooses the newest Kobo or KOReader check-in', () => {
  const newest = latestPairingDevice([
    { label: 'Browser', type: 'webreader', last_seen: '2026-08-30T15:00:00Z' },
    { label: 'Old Kobo', type: 'kobo', last_seen: '2026-08-28T15:00:00Z' },
    { label: 'Kitchen KOReader', type: 'koreader', last_seen: '2026-08-30T14:00:00Z' },
    { label: 'Never synced', type: 'kobo', last_seen: null },
  ]);
  assert.equal(newest?.label, 'Kitchen KOReader');
});

test('device-seen confirmation stays waiting for missing or invalid check-ins', () => {
  assert.equal(latestPairingDevice([]), null);
  assert.equal(latestPairingDevice([
    { label: 'Kobo', type: 'kobo', last_seen: 'not-a-date' },
    { label: 'Browser', type: 'webreader', last_seen: '2026-08-30T15:00:00Z' },
  ]), null);
});

test('device timestamps require a real, round-trippable ISO calendar instant', () => {
  assert.equal(latestPairingDevice([
    { label: 'Impossible Kobo', type: 'kobo', last_seen: '2026-02-30T12:00:00Z' },
  ]), null);
  assert.equal(latestPairingDevice([
    { label: 'Loose Kobo', type: 'kobo', last_seen: '2026-08-30 12:00:00Z' },
  ]), null);
});

test('naive device timestamps are interpreted as UTC in every browser timezone', () => {
  const previousTimezone = process.env.TZ;
  const previousDocument = globalThis.document;
  const previousNow = Date.now;
  process.env.TZ = 'America/New_York';
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: { documentElement: { lang: 'en' } },
  });
  Date.now = () => Date.parse('2026-08-30T16:00:00Z');
  try {
    assert.equal(
      relativeWhen('2026-08-30T12:00:00'),
      relativeWhen('2026-08-30T12:00:00Z'),
    );
  } finally {
    Date.now = previousNow;
    if (previousTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimezone;
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: previousDocument,
    });
  }
});

test('KOReader setup stays discoverable without stock Kobo or a token', () => {
  assert.equal(pairingVisibility(false, false).showKoreader, true);
  assert.equal(pairingVisibility(true, false).showKoreader, true);
  assert.equal(pairingVisibility(false, true).showKoreader, true);
});
