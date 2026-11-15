import assert from 'node:assert/strict';
import test from 'node:test';
import { getPrimaryReadTarget, withLookupMode } from '../src/lib/readerTarget.ts';

test('lookup targets preserve format choice, existing source and passage', () => {
  for (const [formats, expected] of [
    [['EPUB'], '/read/42?lookup=1'],
    [['PDF'], '/view/42/pdf?lookup=1'],
    [['MP3'], '/view/42/mp3?lookup=1'],
  ] as [string[], string][]) {
    const target = getPrimaryReadTarget(42, formats, true);
    assert.ok(target);
    assert.equal(withLookupMode(target, true), expected);
  }
  assert.equal(getPrimaryReadTarget(42, ['EPUB'], false), null);
  assert.equal(withLookupMode('/read/42/epub?source=browser#chapter', true),
    '/read/42/epub?source=browser&lookup=1#chapter');
  assert.equal(withLookupMode('/read/42/epub?lookup=0', true), '/read/42/epub?lookup=1');
  assert.equal(withLookupMode('/read/42?source=browser', false), '/read/42?source=browser');
});
