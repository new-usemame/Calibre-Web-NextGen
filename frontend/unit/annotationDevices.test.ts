import assert from 'node:assert/strict';
import test from 'node:test';
import { effectiveDevice, assignmentOverride, annotationDeviceLabel, assignmentToastReducer } from '../src/lib/annotationDevices.ts';

test('origin attribution survives an absent override; an override wins and neither stays unknown', () => {
  const row = { annotation_id: 'highlight', origin_device_id: 'reader-a', assigned_device_id: null };
  assert.equal(effectiveDevice(row), 'reader-a');
  assert.equal(effectiveDevice({ ...row, assigned_device_id: 'reader-b' }), 'reader-b');
  assert.equal(effectiveDevice({ ...row, origin_device_id: null }), null);
});

test('clearing an override returns to origin without losing the stored override needed by Undo', () => {
  const row = { annotation_id: 'highlight', origin_device_id: 'reader-a', assigned_device_id: 'reader-b' };
  const previous = assignmentOverride(row, {});
  const cleared = assignmentOverride(row, { highlight: null });
  assert.equal(previous, 'reader-b');
  assert.equal(cleared, null);
  assert.equal(effectiveDevice(row, cleared), 'reader-a');
  assert.equal(effectiveDevice(row, previous), 'reader-b');
});

test('an assignable device has its real label even before this book references it', () => {
  assert.equal(annotationDeviceLabel('reader-b', { 'reader-b': { label: 'PocketBook' } }, 'Unknown device', 'Deleted device'), 'PocketBook');
  assert.equal(annotationDeviceLabel(null, {}, 'Unknown device', 'Deleted device'), 'Unknown device');
});

test('dismiss clears a success or failure toast without changing the successful assignment or invoking Undo', () => {
  for (const failed of [0, 1]) {
    const assignments = { highlight: 'reader-b' };
    const toast = { text: 'Assigned to PocketBook.', undo: { highlight: null }, failed, target: 'reader-b' };
    const shown = assignmentToastReducer(null, { type: 'show', toast });
    assert.deepEqual(shown, toast);
    assert.equal(assignmentToastReducer(shown, { type: 'dismiss' }), null);
    assert.deepEqual(assignments, { highlight: 'reader-b' });
  }
});
