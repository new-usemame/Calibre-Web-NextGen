/** Parity and behaviour for the editor's narrowing controls.
 *
 *  Two rules, implemented TWICE -- once in the classic script and once in the
 *  React editor. That is a duplication the single-home lint forbids for the node
 *  rules, and it is deliberate here: these are view filters, they carry no
 *  consequence, and the alternative was dragging React into the classic page.
 *
 *  The duplication is only safe because it is pinned from both sides: the TS
 *  tests below and the Python source assertions in
 *  test_cc_editor_narrowing.py must agree, and a change to one without the other
 *  fails one of the two suites.
 */
import { deepStrictEqual, ok, strictEqual } from 'node:assert/strict';
import { test } from 'node:test';

import { depthOptions, isUnderFocus, narrowRows } from '../src/lib/ccNarrow.ts';

/** A row as the editor builds it. The tree shape is irrelevant to narrowing --
 *  only `path` and `depth` are read -- so the node is kept deliberately thin. */
interface Row { node: { path: string }; depth: number }

function row(path: string, depth: number): Row {
  return { node: { path }, depth };
}

// The tree used throughout, as [path, depth] pairs in DFS order.
//   Arts                     0
//     Painting              1
//       Oil                  2
//     Sculpture             1
//   History                  0
//     Ancient               1
//     Modern                 1
//     Ancient.Egypt         2
const TREE: Row[] = [
  row('Arts', 0),
  row('Arts.Painting', 1),
  row('Arts.Painting.Oil', 2),
  row('Arts.Sculpture', 1),
  row('History', 0),
  row('History.Ancient', 1),
  row('History.Modern', 1),
  row('History.Ancient.Egypt', 2),
];

const pathsOf = (rows: { node: { path: string } }[]): string[] =>
  rows.map((r) => r.node.path);

// ---------------------------------------------------------------- the focus rule

test('with no focus, every row is shown', () => {
  deepStrictEqual(pathsOf(narrowRows(TREE, '', 0)), pathsOf(TREE));
});

test('a focus shows the value itself and everything under it', () => {
  deepStrictEqual(
    pathsOf(narrowRows(TREE, 'History', 0)),
    ['History', 'History.Ancient', 'History.Modern', 'History.Ancient.Egypt'],
  );
});

test('a focus on a leaf shows only that leaf', () => {
  deepStrictEqual(pathsOf(narrowRows(TREE, 'Arts.Painting.Oil', 0)), ['Arts.Painting.Oil']);
});

test('a focus is not fooled by a sibling sharing a textual prefix', () => {
  // Historyics merely starts with "History"; the separator is what makes
  // History.Ancient a child. Same rule as the cycle guard.
  ok(isUnderFocus('Historyics', 'History') === false);
  ok(isUnderFocus('History.Ancient', 'History') === true);
});

test('a focus matches exactly, and its own descendants, and nothing else', () => {
  ok(isUnderFocus('History', 'History'), 'itself');
  ok(isUnderFocus('History.Ancient', 'History'), 'a child');
  ok(isUnderFocus('History.Ancient.Egypt', 'History'), 'a grandchild');
  ok(!isUnderFocus('Historyics', 'History'), 'a prefix-sharing sibling');
  ok(!isUnderFocus('Arts', 'History'), 'an unrelated branch');
});

// -------------------------------------------------------------- the depth rule

test('depth 1 means the top level only', () => {
  deepStrictEqual(pathsOf(narrowRows(TREE, '', 1)), ['Arts', 'History']);
});

test('depth 2 means the top two levels', () => {
  deepStrictEqual(pathsOf(narrowRows(TREE, '', 2)), [
    'Arts', 'Arts.Painting', 'Arts.Sculpture',
    'History', 'History.Ancient', 'History.Modern',
  ]);
});

test('depth 0 means every level', () => {
  deepStrictEqual(pathsOf(narrowRows(TREE, '', 0)), pathsOf(TREE));
});

test('depth is measured from the FOCUSED root, not the true one', () => {
  /* The case that makes the absolute reading useless: focus on a node two
   * levels down and ask for two levels. Absolute would show one row -- the
   * focus itself and nothing else -- and read as a broken filter.
   *
   * "Two levels from History" is History plus its children, so Ancient.Egypt
   * (the third level) is out. */
  deepStrictEqual(
    pathsOf(narrowRows(TREE, 'History', 2)),
    ['History', 'History.Ancient', 'History.Modern'],
  );
  deepStrictEqual(
    pathsOf(narrowRows(TREE, 'History.Ancient', 1)),
    ['History.Ancient'],
  );
  deepStrictEqual(
    pathsOf(narrowRows(TREE, 'History.Ancient', 2)),
    ['History.Ancient', 'History.Ancient.Egypt'],
  );
});

test('the two controls compose', () => {
  deepStrictEqual(
    pathsOf(narrowRows(TREE, 'History', 1)),
    ['History'],
  );
});

test('a focus whose value has since been deleted is ignored, not obeyed', () => {
  /* A focus left pointing at a value that was renamed or deleted under it.
   * Obeying it would filter every row out and leave an empty panel whose only
   * exit is a control the user has to guess at. */
  deepStrictEqual(pathsOf(narrowRows(TREE, 'History.Gone', 0)), pathsOf(TREE));
  ok(narrowRows(TREE, 'History.Gone', 0).length > 0, 'something must stay visible');
});

test('depth never goes negative for a row shallower than the focus', () => {
  // Defensive: cannot happen with a live focus, but a stale one must not clamp
  // every row to depth 0 and hide the tree.
  const shallow = [row('Arts', 0)];
  deepStrictEqual(pathsOf(narrowRows(shallow, 'Nope.Deeper', 1)), ['Arts']);
});

// ------------------------------------------------------------------- options

test('the level options cover every level actually present', () => {
  deepStrictEqual(depthOptions(TREE), [1, 2, 3]);
});

test('a single-level tree offers one option', () => {
  deepStrictEqual(depthOptions([row('A', 0), row('B', 0)]), [1]);
});

test('an empty tree still offers one option rather than none', () => {
  deepStrictEqual(depthOptions([]), [1], 'never an empty select');
});

// ------------------------------------------------------------------- the contract

test('narrowing only ever hides rows, never adds them', () => {
  const all = pathsOf(TREE);
  for (const focus of ['', 'Arts', 'History', 'History.Ancient']) {
    for (let limit = 0; limit <= 4; limit += 1) {
      const shown = new Set(pathsOf(narrowRows(TREE, focus, limit)));
      ok(shown.size <= TREE.length, 'never more rows than it started with');
      for (const p of shown) {
        ok(all.includes(p), `${p} must be one of the original rows`);
      }
    }
  }
});

test('a filtered row keeps its identity, so its actions stay correct', () => {
  /* The rows are the same objects, not copies with rewritten paths -- the
   * editor renders each row's actions from its own path, and a copied row would
   * carry a stale one. */
  const filtered = narrowRows(TREE, 'History', 0);
  const wanted = 'History.Modern';
  strictEqual(
    filtered.find((r) => r.node.path === wanted),
    TREE.find((r) => r.node.path === wanted),
    'the very same row object',
  );
});