import assert from 'node:assert/strict';
import test from 'node:test';

import {
  displayUserCode, formatTypedCode, isOwnComputerAddress, minutesAgo, normalizeUserCode,
  pairingDeepLink,
} from '../src/lib/koreaderPairing.ts';

test('a code is accepted however loosely it is typed', () => {
  for (const typed of ['K7M4-QX2P', 'k7m4qx2p', ' K7M4 QX2P ', 'k7m4-qx2p']) {
    assert.equal(normalizeUserCode(typed), 'K7M4QX2P', typed);
  }
});

test('a code the device could never show is refused before it is sent', () => {
  // Wrong length, a vowel, a zero or a one, and punctuation other than the dash.
  for (const typed of ['K7M4-QX2', 'K7M4-QX2PP', 'K7M4-QXAP', 'K7M4-QX0P', 'K7M4-QX1P',
    'K7M4.QX2P', '', 'K7M4-QX2P;']) {
    assert.equal(normalizeUserCode(typed), null, typed);
  }
});

test('the code box shows what the device shows while it is typed', () => {
  assert.equal(formatTypedCode('k7m'), 'K7M');
  assert.equal(formatTypedCode('k7m4q'), 'K7M4-Q');
  assert.equal(formatTypedCode('K7M4-QX2P-EXTRA'), 'K7M4-QX2P');
  assert.equal(displayUserCode('K7M4QX2P'), 'K7M4-QX2P');
});

test('the deep link opens the code box and fills in only a real code', () => {
  assert.deepEqual(pairingDeepLink('?pair=1'), { open: true, code: null });
  assert.deepEqual(pairingDeepLink('?pair=1&code=K7M4QX2P'), { open: true, code: 'K7M4QX2P' });
  assert.deepEqual(pairingDeepLink('?code=k7m4-qx2p'), { open: true, code: 'K7M4QX2P' });
  assert.deepEqual(pairingDeepLink('?pair=1&code=%3Cscript%3E'), { open: true, code: null });
  assert.deepEqual(pairingDeepLink(''), { open: false, code: null });
});

test('how long ago a device asked reads in minutes, and not at all under one', () => {
  const now = Date.parse('2026-09-23T17:10:00Z');
  assert.equal(minutesAgo('2026-09-23T17:07:00Z', now, 'en'), '3 minutes ago');
  assert.equal(minutesAgo('2026-09-23T17:09:30Z', now, 'en'), null);
  assert.equal(minutesAgo(null, now, 'en'), null);
  assert.equal(minutesAgo('not a time', now, 'en'), null);
  assert.equal(minutesAgo('2026-09-23T17:07:00Z', now, 'fr'), 'il y a 3 minutes');
});

test('an address the e-reader cannot reach is recognised however it is typed', () => {
  for (const own of ['http://localhost:8083', 'localhost:8083', 'http://127.0.0.1:8083',
    '127.0.1.1', 'http://[::1]:8083', 'https://books.localhost', 'http://0.0.0.0:8083']) {
    assert.equal(isOwnComputerAddress(own), true, own);
  }
  for (const reachable of ['http://192.168.1.20:8083', '192.168.1.20:8083',
    'https://books.example.com/cwa', 'http://[fd00::5]:8083', 'localhost.example.com', '', 'http://']) {
    assert.equal(isOwnComputerAddress(reachable), false, reachable);
  }
});
