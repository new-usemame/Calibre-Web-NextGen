/* The per-node availability flags are published by the server, not re-derived here.
 *
 * Whether a value can be deleted, or used as a merge source, depends only on
 * that value — so `cps/hierarchy.py::node_availability` decides it once and
 * `cps/api/columns.py::_node_to_json` publishes it on every node.
 *
 * Before this, `CcValueEditor` recomputed both from `node.children.length`, and
 * `ccMerge.ts` re-implemented the guard in full. Two copies of one rule, which
 * is exactly how the interfaces had started to disagree: the client could offer
 * an operation the server then refused.
 *
 * These tests are structural because the rule is TypeScript-shaped: they check
 * the editor READS the published flag and no longer derives it. The flag's own
 * correctness is pinned server-side, in tests/unit/test_hierarchy_node_rules.py
 * (`test_the_published_flags_are_the_rules_own_verdict`), which runs the real
 * planners and demands the same answer.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const source = (relative: string) =>
  fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

test('the server publishes availability on every node', () => {
  const columns = source('../cps/api/columns.py');
  const at = columns.indexOf('def _node_to_json');
  assert.notEqual(at, -1, '_node_to_json must exist');
  const next = columns.indexOf('\ndef ', at + 1);
  const body = columns.slice(at, next === -1 ? undefined : next);

  assert.match(
    body,
    /hierarchy\.node_availability\(\s*node\s*,/,
    'the wire node must carry the flag hierarchy.node_availability computes',
  );
  assert.match(body, /"available"/, 'the flag must actually be serialised, not just computed');

  // The sibling list is not optional detail. `node_availability(node)` with no
  // second argument returns every move flag refused, so a caller that forgets
  // it produces a tree where nothing can be moved and nothing looks wrong --
  // which is exactly what happened on the classic side while this was being
  // built. Assert the argument is passed, not that a call happened.
  assert.match(
    body,
    /node_availability\([\s\S]*?sibling/,
    '_node_to_json must pass the sibling list, or every move flag reads as refused',
  );
});

test('the editor reads the published flag instead of deriving it', () => {
  const editor = source('src/pages/CcValueEditor.tsx');

  assert.match(
    editor,
    /available\?\.can_merge_source/,
    'the merge-source rule must come from the server, not children.length',
  );
  assert.match(
    editor,
    /available\s*\?\s*node\.available\.can_delete/,
    'the delete rule must come from the server, not children.length',
  );
  assert.doesNotMatch(
    editor,
    /hasChildren/,
    'the hasChildren derivation is the duplicated copy this replaced',
  );
});

test('ccMerge no longer re-implements the leaves-only rule', () => {
  const merge = source('src/lib/ccMerge.ts');
  // Strip the leading block comment: it explains the rule in prose, and "has
  // children" has to appear there. Only executable code is under test here.
  const code = merge.replace(/^\/\*[\s\S]*?\*\//, '').replace(/^\/\*\*[\s\S]*?\*\//gm, '');

  // The guard now asks the server, so the predicate it is handed answers
  // "may this be a source", not "does this node have children".
  assert.match(
    code,
    /sources\.find\(\(p\) => !canBeSource\(p\)\)/,
    'the source guard must be a NOT over the published flag, so the polarity cannot be flipped by accident',
  );
  assert.doesNotMatch(
    code,
    /hasChildren/,
    'the children rule belongs to the server now',
  );
  // What genuinely cannot be published stays, because it depends on selection.
  assert.match(code, /mergeSourcePaths/, 'the destination-exclusion rule is client-side');
});

test('the published flags are optional, so an older server still renders', () => {
  const api = source('src/lib/api.ts');
  assert.match(
    api,
    /available\?: CcNodeAvailability/,
    'CcNode.available must be optional: a newly-deployed SPA can meet an older API worker',
  );

  const editor = source('src/pages/CcValueEditor.tsx');
  assert.match(
    editor,
    /\?\?\s*\(nodeByPath\.get\(p\)\?\.children\.length \?\? 0\) === 0/,
    'the merge-source read needs a fallback for a server that does not send the flag',
  );
});

test('what is NOT published is the part that needs the selection', () => {
  // The omission is deliberate and worth pinning: "can this row receive my
  // cut?" cannot be answered per node, because it depends on what the user has
  // currently cut. Publishing a per-node can_move_into would be wrong, not just
  // redundant — it would be true for every row until a value is cut.
  const hierarchy = source('../cps/hierarchy.py');
  const at = hierarchy.indexOf('def node_availability');
  assert.notEqual(at, -1);
  const body = hierarchy.slice(at, hierarchy.indexOf('\ndef ', at + 1));

  assert.doesNotMatch(body, /can_move_into|can_merge_into/);
  assert.match(body, /can_merge_source/);
  assert.match(body, /can_delete/);
});