/* The cycle rule, checked against the table BOTH implementations share.
 *
 * `canPasteInto` in src/lib/ccPaste.ts is the one deliberate client copy of a
 * server rule (hierarchy.can_place_under). It has to exist: whether a row may
 * receive a moved value depends on the value the user has currently cut, which
 * the server never sees, so there is no per-node flag that could express it.
 *
 * Being a copy means its agreement has to be MAINTAINED rather than structural,
 * and until this file existed nothing maintained it -- both sides had their own
 * cases, in two languages, and a case added to one was invisible to the other.
 *
 * So the cases live in ONE file, unit/ccCycleCases.json, and both languages read
 * it:
 *
 *   this file          -> canPasteInto      (TypeScript)
 *   tests/unit/..._single_home.py -> can_place_under (Python)
 *
 * A case added on one side and not the other is now impossible to express,
 * which is the whole point.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { canPasteInto } from '../src/lib/ccPaste.ts';

interface CycleCase {
  name: string;
  why?: string;
  cut: string;
  onto: string;
  allowed: boolean;
}

const table = JSON.parse(
  fs.readFileSync(path.join(process.cwd(), 'unit', 'ccCycleCases.json'), 'utf8'),
) as { cases: CycleCase[] };

test('the shared table has cases, and names them', () => {
  // A table that silently emptied would make every check below vacuous.
  assert.ok(table.cases.length >= 8,
    `the shared cycle table shrank to ${table.cases.length} cases; ` +
    'each implementation would then be untested on the awkward inputs');
  for (const c of table.cases) {
    assert.ok(c.name, 'every case needs a name, or a failure is unreadable');
  }
});

for (const c of table.cases) {
  test(`cycle rule: ${c.name}`, () => {
    assert.equal(canPasteInto(c.cut, c.onto), c.allowed,
      `${c.why ?? ''} -- cut ${c.cut || '<none>'} onto ${c.onto || '<root>'}`);
  });
}

test('the table keeps the cases that have actually been got wrong', () => {
  // A table of only obvious cases would pass forever while the interesting
  // disagreement went unnoticed. These three are the ones with history.
  const has = (cut: string, onto: string) =>
    table.cases.some((c) => c.cut === cut && c.onto === onto);

  assert.ok(has('History.AncientHistory.India', 'History'),
    'the reported case must be in the table: pasting onto an own ancestor is ALLOWED');
  assert.ok(has('TestNode', 'TestNodeOther'),
    'a shared prefix is not a path segment, so it must be in the table');
  assert.ok(has('TestNode', 'TestNode.TestSubNode.TestDeepNode'),
    'the cycle guard must be checked at depth, not just one level');
});

test('the classic editor and the SPA share this rule, not a copy of it', () => {
  // The classic editor's canPasteInto is the other consumer of the same rule,
  // and it must agree with the SPA's. This reads both and compares their
  // verdict on every case in the table.
  const classic = fs.readFileSync(
    path.join(process.cwd(), '..', 'cps', 'static', 'js', 'cc_value_editor.js'),
    'utf8');

  assert.match(classic, /can_place_under/,
    'the classic editor must name the server-side rule it mirrors, so a reader ' +
    'knows which copy is authoritative');

  for (const c of table.cases) {
    // Reproduce the classic editor's expression inline and compare. Kept as
    // the same two-part test the file uses, so a change to one shows up.
    const classicVerdict =
      !!c.cut && c.onto !== c.cut && !c.onto.startsWith(c.cut + '.');
    assert.equal(classicVerdict, c.allowed,
      `the classic editor's expression disagrees on: ${c.name}`);
  }
});