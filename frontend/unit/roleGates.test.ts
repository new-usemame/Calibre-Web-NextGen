import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { Me } from '../src/lib/api.ts';
import {
  canConvertWithReflow, canDeleteBooks, canDownloadBooks, canEditBookCover, canEditShelf, canReadBooks,
} from '../src/lib/permissions.ts';
import { getPrimaryReadTarget, getReaderContentUrl } from '../src/lib/readerTarget.ts';

function account(role: Record<string, boolean>): Me {
  return {
    id: 1,
    name: 'role-probe',
    locale: 'en',
    theme: 'light',
    role,
  };
}

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

test('reader CTA and content route use viewer independently of download', () => {
  const probes = [
    { name: 'download-only', me: account({ viewer: false, download: true }), canRead: false },
    { name: 'viewer-only', me: account({ viewer: true, download: false }), canRead: true },
    { name: 'viewer-and-download', me: account({ viewer: true, download: true }), canRead: true },
  ];

  for (const probe of probes) {
    assert.equal(canReadBooks(probe.me), probe.canRead, probe.name);
    assert.equal(
      getPrimaryReadTarget(197, ['EPUB'], canReadBooks(probe.me)),
      probe.canRead ? '/read/197' : null,
      probe.name,
    );
  }

  assert.equal(canDownloadBooks(probes[0].me), true);
  assert.equal(canDownloadBooks(probes[1].me), false);
  assert.equal(getReaderContentUrl(197, 'EPUB'), '/show/197/epub');

  // Rendered shared-resource controls and denied roles are covered by the
  // real browser flow in shared-book-continuation.spec.ts.
});

test('all destructive book CTAs require delete-books and edit together', () => {
  assert.equal(canDeleteBooks(account({ delete_books: true, edit: false })), false);
  assert.equal(canDeleteBooks(account({ delete_books: false, edit: true })), false);
  assert.equal(canDeleteBooks(account({ delete_books: true, edit: true })), true);

  const detail = source('../src/pages/BookDetail.tsx');
  const edit = source('../src/pages/EditBook.tsx');
  const bulk = source('../src/components/BulkBar.tsx');
  assert.match(detail, /const canDelete = canDeleteBooks\(me\)/);
  // The whole-book delete in the gear menu is admin-only (operator instruction;
  // the server keeps its own delete+edit check). It never sits among the
  // ordinary actions.
  assert.match(detail, /if \(me\?\.role\?\.admin\) \{[\s\S]*label: t\('Admin only'\),[\s\S]*danger: true/);
  assert.match(detail, /testId: 'menu-delete-book'/);
  // Per-format delete in the Files section keeps the delete+edit conjunction.
  assert.match(detail, /const canDelete = canDeleteBooks\(me\);[\s\S]*\{canDelete && \(/);
  assert.match(edit, /\{canDeleteBooks\(me\) && \(/);
  assert.match(bulk, /const canDelete = canDeleteBooks\(me\)/);
});

test('Reflow is offered to the same accounts the server lets convert: edit or admin', () => {
  // cps/api/reflow.py::_require_edit accepts role_edit() or role_admin(), the
  // predicate of editbooks.edit_required. A link gated on edit alone hid a
  // conversion the server would have accepted from an admin without the edit bit.
  assert.equal(canConvertWithReflow(account({ edit: true, admin: false })), true);
  assert.equal(canConvertWithReflow(account({ edit: false, admin: true })), true);
  assert.equal(canConvertWithReflow(account({ edit: false, admin: false })), false);
  assert.equal(canConvertWithReflow(undefined), false);
});

test('the cover editor is offered only where some cover can be saved', () => {
  // [account, in the reader's library, may open the editor]
  const probes: [string, Me | undefined, boolean, boolean][] = [
    ['reader, own book', account({ viewer: true }), true, true],
    // A public shelf alone: the private cover route answers 404 and the
    // library cover needs the edit role, so the editor could only fail.
    ['reader, book shared by a public shelf', account({ viewer: true }), false, false],
    ['Global Library reader, book outside the library', account({ viewer: true, browse_global: true }), false, true],
    ['editor, book outside the library', account({ edit: true }), false, true],
    ['admin, book outside the library', account({ admin: true }), false, true],
    ['guest', account({ anonymous: true, viewer: true }), true, false],
    ['account still loading', undefined, true, false],
  ];
  for (const [name, me, inLibrary, expected] of probes) {
    assert.equal(canEditBookCover(me, inLibrary), expected, name);
  }
});

test('a shelf is offered for adding or removing exactly where the server allows the change', () => {
  // Owners may edit their own shelves; the role grants cross-account public edits.
  const shelves: [string, { is_public: boolean; is_owner: boolean }][] = [
    ['own private shelf', { is_public: false, is_owner: true }],
    ["another reader's private shelf", { is_public: false, is_owner: false }],
    ['own public shelf', { is_public: true, is_owner: true }],
    ["another reader's public shelf", { is_public: true, is_owner: false }],
  ];
  const offered = (me: Me | undefined) =>
    shelves.filter(([, shelf]) => canEditShelf(me, shelf)).map(([name]) => name);

  assert.deepEqual(offered(account({ edit_shelfs: true })),
    ['own private shelf', 'own public shelf', "another reader's public shelf"]);
  assert.deepEqual(offered(account({ edit_shelfs: false })),
    ['own private shelf', 'own public shelf']);
  assert.deepEqual(offered(account({ edit_shelfs: true, anonymous: true })), []);
  assert.deepEqual(offered(undefined), []);
});
