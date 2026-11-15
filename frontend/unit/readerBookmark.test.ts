import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readerBookmarkBody } from '../src/lib/readerBookmark.ts';

test('a Browser-only save keeps its false share flag on the wire, with or without a percentage', () => {
  for (const percentage of [0, 30, 100, null, undefined]) {
    const body = JSON.parse(JSON.stringify(readerBookmarkBody('epubcfi(place)', percentage, false)));
    assert.equal(body.share_with_devices, false);
    assert.equal(body.bookmark, 'epubcfi(place)');
    assert.equal(body.format, 'epub');
    assert.equal(Object.prototype.hasOwnProperty.call(body, 'percentage'), percentage != null);
    if (percentage != null) assert.equal(body.percentage, percentage);
  }
});

test('normal reading explicitly shares and payloads follow the current choice', () => {
  const preview = readerBookmarkBody('preview', 30, false);
  const reading = readerBookmarkBody('newer', 40, true);
  assert.deepEqual(reading, {
    format: 'epub', bookmark: 'newer', percentage: 40, share_with_devices: true,
  });
  assert.equal(preview.share_with_devices, false);
  assert.equal(preview.bookmark, 'preview');
});
