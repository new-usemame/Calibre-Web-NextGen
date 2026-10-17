/* The merge rules for the custom-column value editor.
 *
 * "Merge" is a per-row button: the row it is pressed on is the value that
 * SURVIVES, and every other ticked value is folded into it. That inverts the
 * old multi-select form, which made the user type the destination.
 *
 * Invariants this file pins:
 *
 *   1. The destination is never among the sources. Sending it would make the
 *      server answer 400 "A value cannot be merged into itself".
 *   2. Only the SOURCES must be leaves. That is the rule
 *      cps/api/cc_values.py::merge_cc_values enforces -- it loops over `paths`
 *      and rejects a source whose node has children, and never inspects `into`.
 *
 *   The DESTINATION is therefore free to have sub-values. Merging "Ancient
 *   Greece" into "Greece" when "Greece" has a child "Sparta" is well defined:
 *   the source row is repointed at the survivor and the children are untouched.
 *   An earlier version refused that, blocking an operation the server accepts.
 *
 * Invariant 2 is no longer RE-DERIVED here. The server publishes it on every
 * node as `available.can_merge_source` (hierarchy.node_availability), so the
 * predicate below stands in for that flag rather than inspecting children. Its
 * polarity matters: `canBeSource` is already the server's answer, so the test
 * helper is written the same way the real one is. Inverting it here would test
 * a rule nothing ships.
 *
 * Extracted from the .tsx because the unit runner cannot import TSX -- same
 * reason canPasteInto lives in ccPaste.ts.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { mergeAvailability, mergeSourcePaths } from '../src/lib/ccMerge.ts';

/** A tree shaped like the reported case: Greece has the child Sparta.
 *  Stands in for `node.available.can_merge_source`, so Greece is not a source. */
const canBeSource = (p: string) => p !== 'Greece';

test('a merge consumes every ticked value except the destination', () => {
  assert.deepEqual(mergeSourcePaths(['Arts', 'History'], 'Arts'), ['History']);
});

test('the survivors keep the order they were ticked in', () => {
  assert.deepEqual(mergeSourcePaths(['C', 'A', 'B'], 'A'), ['C', 'B']);
});

test('a lone tick has nothing to merge into it', () => {
  assert.deepEqual(mergeSourcePaths(['Arts'], 'Arts'), []);
});

test('a leaf can be merged into a node that has sub-values', () => {
  // The reported case: "Ancient Greece" -> "Greece", where Greece.Sparta exists.
  const got = mergeAvailability(['Ancient Greece', 'Greece'], 'Greece', canBeSource);
  assert.equal(got.ok, true, JSON.stringify(got));
  // deepEqual, not equal: `node:assert/strict` makes equal a strictEqual, which
  // compares an array by identity and can never match a fresh literal.
  assert.deepEqual(mergeSourcePaths(['Ancient Greece', 'Greece'], 'Greece'), ['Ancient Greece']);
});

test('a node with sub-values can be the destination of several leaves', () => {
  const got = mergeAvailability(
    ['Ancient Greece', 'Roman Greece', 'Greece'], 'Greece', canBeSource);
  assert.equal(got.ok, true);
  assert.deepEqual(
    mergeSourcePaths(['Ancient Greece', 'Roman Greece', 'Greece'], 'Greece'),
    ['Ancient Greece', 'Roman Greece']);
});

test('a SOURCE with sub-values cannot be merged away', () => {
  // The server refuses this: folding a parent would orphan its children. It
  // reaches the client as can_merge_source === false, which is what canBeSource
  // reports for Greece.
  const got = mergeAvailability(['Greece', 'Arts'], 'Arts', canBeSource);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'source-has-children');
  assert.equal(got.offendingSource, 'Greece', 'the tooltip must name the blocker');
});

test('a destination that is not a valid source is still a valid destination', () => {
  // The polarity of the published flag must not leak into the destination check.
  // canBeSource('Greece') is false, yet Greece can be the SURVIVOR: the leaf rule
  // applies to sources only, and the destination is removed from the source list
  // before the rule runs at all. Ordering this wrongly would make it impossible
  // to merge into any value that happens to have children -- the exact operation
  // that used to be blocked.
  const got = mergeAvailability(
    ['Ancient Greece', 'Roman Greece', 'Greece'], 'Greece', canBeSource);
  assert.equal(got.ok, true, JSON.stringify(got));
  assert.deepEqual(mergeSourcePaths(['Ancient Greece', 'Roman Greece', 'Greece'], 'Greece'),
    ['Ancient Greece', 'Roman Greece']);
});

test('an unticked row cannot host the merge', () => {
  // Otherwise every row in the tree looks actionable, and clicking one merges a
  // selection the user never pointed at. The source predicate is irrelevant here
  // and deliberately permissive, so this can only fail on the tick gate.
  const got = mergeAvailability(['Arts', 'History'], 'Other', () => true);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'not-ticked');
});

test('a lone tick cannot host a merge, because one value is a rename', () => {
  const got = mergeAvailability(['Arts'], 'Arts', () => true);
  assert.equal(got.ok, false);
  assert.equal(got.reason, 'needs-another');
});

test('needs-another is reported before source-has-children', () => {
  // Both apply. If 'source-has-children' won, the tooltip would blame a value
  // the user has not ticked yet, when the real problem is the empty selection.
  const got = mergeAvailability(['Greece'], 'Greece', canBeSource);
  assert.equal(got.reason, 'needs-another');
});