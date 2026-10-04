import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ACQUISITION_CANCELLABLE_STATES,
  ACQUISITION_TERMINAL_STATES,
  isAcquisitionPending,
} from '../src/lib/acquisitionJobStates.ts';

// The activity list polls only while something is still pending. Getting this
// set wrong is silent: the page simply stops asking, and the user watches a
// stale row forever.

test('a request waiting on an administrator is still pending', () => {
  // The original bug. Approval is not a state the user can leave and come
  // back to — the page has to keep watching, because the administrator may
  // approve at any moment and the import then runs to completion.
  assert.equal(isAcquisitionPending('awaiting_approval'), true);
});

test('every state the worker moves through is pending', () => {
  for (const state of ['queued', 'resolving', 'downloading', 'staged', 'publishing', 'importing']) {
    assert.equal(isAcquisitionPending(state), true, `${state} should keep the page watching`);
  }
});

test('imported, failed, cancelled and rejected settle a request', () => {
  assert.deepEqual([...ACQUISITION_TERMINAL_STATES].sort(), ['cancelled', 'failed', 'imported', 'rejected']);
  for (const state of ['imported', 'failed', 'cancelled']) {
    assert.equal(isAcquisitionPending(state), false, `${state} should stop the polling`);
  }
});

test('an unfamiliar state keeps the page watching rather than going quiet', () => {
  // Pending is derived by negation precisely so a state a newer server
  // invented cannot silently strand a request the way awaiting_approval did.
  assert.equal(isAcquisitionPending('some_state_a_later_server_added'), true);
});

test('source_busy is an error code, not a state, so it never settles a request', () => {
  // The server writes it on a FAILED job's error_code; it is never written to
  // the state column, and treating it as a state produced a dead status label.
  assert.equal(ACQUISITION_TERMINAL_STATES.has('source_busy'), false);
});

test('cancel is offered only where the server accepts it', () => {
  // storage.py refuses anything outside this set, so offering the button
  // elsewhere produces a 409 the user never asked for.
  assert.deepEqual(
    [...ACQUISITION_CANCELLABLE_STATES].sort(),
    ['awaiting_approval', 'awaiting_selection', 'downloading', 'queued', 'resolving', 'staged'],
  );
  for (const state of ['publishing', 'importing', 'imported', 'failed', 'cancelled']) {
    assert.equal(ACQUISITION_CANCELLABLE_STATES.has(state), false,
      `${state} is past the point the server will stop it`);
  }
});

test('a rejected request stops polling and cannot be cancelled', () => {
  assert.equal(isAcquisitionPending('rejected'), false);
  assert.equal(ACQUISITION_CANCELLABLE_STATES.has('rejected'), false);
});
