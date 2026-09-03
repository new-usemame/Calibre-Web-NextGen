import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { getPrimaryReadTarget } from '../../src/lib/readerTarget.ts';

const globalLibrarySource = readFileSync(
  new URL('../../src/pages/GlobalLibrary.tsx', import.meta.url),
  'utf8',
);
const bookCardSource = readFileSync(
  new URL('../../src/components/BookCard.tsx', import.meta.url),
  'utf8',
);
const cardCall = globalLibrarySource.match(/return <BookCard[\s\S]*?\/>;/)?.[0] ?? '';

function globalCardCanRead(owned: boolean, viewer: boolean): boolean {
  return owned && viewer;
}

describe('Global Library card permissions', () => {
  test('an owned book exposes Read to a viewer-authorized account', () => {
    assert.ok(
      cardCall.includes('canRead={owned && !!me?.role?.viewer}'),
      'GlobalLibrary must pass both membership and the viewer role to BookCard',
    );
    assert.equal(globalCardCanRead(true, true), true);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], globalCardCanRead(true, true)), '/read/42');
  });

  test('an owned book exposes no Read target without viewer permission', () => {
    assert.equal(globalCardCanRead(true, false), false);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], globalCardCanRead(true, false)), null);
    assert.ok(
      bookCardSource.includes('const readTarget = getPrimaryReadTarget(book.id, book.formats, canRead);'),
      'BookCard must derive its Read action from the explicit canRead prop',
    );
  });

  test('an unowned book opens details and stays Add-only even for a viewer', () => {
    assert.equal(globalCardCanRead(false, true), false);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], globalCardCanRead(false, true)), null);
    assert.ok(cardCall.includes('detailsEnabled'));
    assert.ok(!cardCall.includes('detailsEnabled={owned}'));
    assert.ok(cardCall.includes('onAddToLibrary={owned ? undefined : addBook}'));
    assert.ok(
      bookCardSource.includes("const hasAddAction = membership === 'unowned' && !!onAddToLibrary;"),
    );
    assert.match(
      bookCardSource,
      /\{hasAddAction \? \([\s\S]*?\) : readTarget && !hideActions \? \(/,
      'the Add action must take precedence over the Read action for unowned cards',
    );
  });
});
