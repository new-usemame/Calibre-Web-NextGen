/* The custom-column VALUE editor is a metadata write surface.
 *
 * Every action it offers — rename, merge, delete, MOVE (cut/paste), create —
 * POSTs through `cps/api/browse.py::_require_metadata_editor()`, which answers
 * 403 without `role.edit`. If the entry point into the editor is not gated on
 * the same role, a signed-in non-editor is shown a fully working-looking panel
 * in which every button is refused, and (because the refusal used to render
 * below the value list) the whole feature reads as "nothing happens".
 *
 * The reported symptom was exactly that: cutting a value produced the
 * "ready to paste" banner, and pasting produced no visible change at all.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const source = (relative: string) =>
  fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

test('the Edit values entry point is gated on the metadata-edit role', () => {
  const browse = source('src/pages/CcBrowse.tsx');

  // The "Edit" toggle that mounts CcValueEditor. Bounded window: an earlier
  // '</div>' appears elsewhere in the file, so an unbounded end index is wrong.
  const at = browse.indexOf('{editing ? (');
  assert.notEqual(at, -1, 'the editing toggle must exist');
  const entry = browse.slice(at, at + 1200);
  assert.match(
    entry,
    /role\?\.edit/,
    'entering value-edit mode must require role.edit — every endpoint it calls 403s without it',
  );
});

test('every value-write endpoint the editor calls requires a metadata editor', () => {
  // If this list ever grows a route that skips the guard, the client-side gate
  // above is no longer sufficient on its own. Read across from frontend/.
  const api = fs.readFileSync(
    path.join(process.cwd(), '..', 'cps', 'api', 'cc_values.py'), 'utf8');
  const writes = [
    'rename_cc_value', 'merge_cc_values',
    'delete_cc_values', 'move_cc_value', 'create_cc_value',
  ];
  for (const fn of writes) {
    const start = api.indexOf('def ' + fn + '(');
    assert.notEqual(start, -1, fn + ' must exist');
    const next = api.indexOf('\n@api_v1.route', start);
    const body = api.slice(start, next === -1 ? undefined : next);
    assert.match(
      body,
      /_require_metadata_editor\(\)/,
      fn + ' must call _require_metadata_editor()',
    );
  }
});

test('an editor refusal is rendered beside the paste banner, not below the tree', () => {
  const editor = source('src/pages/CcValueEditor.tsx');
  const bannerAt = editor.indexOf('styles.banner');
  const treeAt = editor.indexOf('styles.tree');
  assert.ok(bannerAt !== -1 && treeAt !== -1);
  assert.ok(
    bannerAt < treeAt,
    'the error/alert markup must sit before the value list, so a refusal is on the first screenful',
  );
  assert.match(editor, /role="alert"/, 'a failed move/paste must be announced as an alert');
});

test('Paste is not offered as a live control when nothing is held', () => {
  // pasteAvailability() used to answer ok === true with an empty buffer, which
  // enabled a button that silently did nothing.
  const paste = source('src/lib/ccPaste.ts');
  assert.match(paste, /'nothing-to-paste'/);
  assert.match(paste, /if \(!cutPath\) return \{ ok: false, reason: 'nothing-to-paste' \}/);
});

test('valid paste destinations are marked before any row is ticked', () => {
  // Otherwise the only rows that light up are the already-ticked ones, and the
  // user has no reason to tick a destination they cannot see is one.
  const editor = source('src/pages/CcValueEditor.tsx');
  assert.match(
    editor,
    /isValidPasteTarget/,
    'the row highlight must come from the cycle rule, not from pasteState.ok',
  );
  assert.match(editor, /isPasteTarget \? styles\.rowPasteTarget/);
});
