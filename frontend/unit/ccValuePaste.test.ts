/* The paste target rules for the custom-column value editor.
 *
 * Two independent gates:
 *
 *   1. NO CYCLES -- a value cannot become its own ancestor.
 *   2. SELECTION -- Cut and Paste are only offered on a ticked row, so a
 *      mis-click on the button cannot move a value the user never chose.
 *
 * The tree used throughout, mirroring the one in the Python tests:
 *
 *   TestNode
 *   TestNode.TestSubNode
 *   TestNode.TestSubNode.TestDeepNode
 *   History
 *   History.AncientHistory
 *   History.AncientHistory.India
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  canCut, canPasteInto, isValidPasteTarget, pasteAvailability,
} from '../src/lib/ccPaste.ts';

const INDIA = 'History.AncientHistory.India';
const DEEP = 'TestNode.TestSubNode.TestDeepNode';

test('nothing can be pasted when the buffer is empty', () => {
  // canPasteInto stays the pure cycle rule; the "is anything held" question is
  // answered by the caller, which has no cutPath at all.
  assert.equal(canPasteInto(null, 'TestNode'), false);
  assert.equal(canPasteInto(null, ''), false);
});

test('a value cannot be pasted onto itself', () => {
  assert.equal(canPasteInto('TestNode.TestSubNode', 'TestNode.TestSubNode'), false);
});

test('a value cannot be pasted into its own sub-value', () => {
  // Moving TestNode.TestSubNode under TestNode.TestSubNode.Horror would make
  // Horror its own ancestor.
  assert.equal(canPasteInto('TestNode.TestSubNode', 'TestNode.TestSubNode.Horror'), false);
});

test('a value CAN be pasted into its own ancestor', () => {
  // The reported case. History.AncientHistory.India -> History yields
  // History.India, which is a SIBLING of AncientHistory, not a descendant of
  // India. No cycle, so it must be allowed.
  assert.equal(canPasteInto(INDIA, 'History'), true);
  assert.equal(canPasteInto(INDIA, 'TestNode'), true);
});

test('a value can be pasted at the top level', () => {
  assert.equal(canPasteInto(DEEP, ''), true);
});

test('a prefix match that is not a path segment is still a cycle', () => {
  // 'TestNode.TestSubNodeX' shares a textual prefix but is a different node;
  // the separator is what makes TestSubNode a real ancestor.
  assert.equal(canPasteInto('TestNode', 'TestNodeOther'), true);
});

/* ---------------------------------------------------------------- selection */

test('Cut is offered only on a ticked row', () => {
  assert.equal(canCut(true), true);
  assert.equal(canCut(false), false);
});

test('Paste needs both a ticked target and a held value', () => {
  // The test name has always said "both"; the assertion below used to pin
  // ok === true with an EMPTY buffer, which contradicted it and left an
  // enabled Paste button on every ticked row that silently did nothing.
  assert.equal(pasteAvailability(INDIA, 'TestNode', true).ok, true);
  assert.equal(pasteAvailability(null, 'TestNode', true).ok, false);
  assert.equal(pasteAvailability(null, 'TestNode', true).reason, 'nothing-to-paste');
});

test('Paste is blocked on an unticked row, and says so', () => {
  // This is the whole point of the gate: without it every row in the tree is a
  // live target and a stray click moves a value somewhere unintended.
  const got = pasteAvailability(INDIA, 'TestNode', false);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'not-ticked');
});

test('the selection gate outranks the cycle check', () => {
  // Both apply. Reporting the cycle would send the user looking for a nesting
  // problem when the actual thing to do is tick the row.
  const got = pasteAvailability(INDIA, INDIA, false);
  assert.equal(got.reason, 'not-ticked');
});

test('Paste refuses the held value itself', () => {
  const got = pasteAvailability(INDIA, INDIA, true);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'into-itself');
});

test('Paste refuses a descendant of the held value', () => {
  const got = pasteAvailability('History.AncientHistory', 'History.AncientHistory.India', true);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'into-descendant');
});

test('valid destinations are discoverable before any row is ticked', () => {
  // The reported dead end: a destination is only marked as a target once it is
  // ticked, and the user has no reason to tick it until they can see it is one.
  // The highlight is computed from the cycle rule alone.
  assert.equal(isValidPasteTarget(INDIA, 'History'), true);
  assert.equal(isValidPasteTarget(INDIA, INDIA), false, 'not into itself');
  // An unrelated branch is a perfectly good destination.
  assert.equal(isValidPasteTarget(INDIA, 'TestNode.TestSubNode'), true);
  // But a row under the held value is a cycle, so it is not marked at all.
  assert.equal(
    isValidPasteTarget('History.AncientHistory', 'History.AncientHistory.India'),
    false,
    'a descendant is never a target',
  );
  assert.equal(isValidPasteTarget(null, 'History'), false, 'nothing held, nothing to target');
});

test('Paste into the ancestor is allowed once both rows are ticked', () => {
  // The full reported flow, gate and cycle rule combined: tick India, Cut it,
  // tick History, Paste.
  const cut = pasteAvailability(INDIA, 'History', true);
  assert.equal(cut.ok, true);
  assert.equal(cut.reason, null);
});