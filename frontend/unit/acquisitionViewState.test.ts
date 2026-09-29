import assert from 'node:assert/strict';
import test from 'node:test';

import {
  acquisitionRequesterLabel,
  acquisitionSectionView,
  connectionEnableRejected,
  connectionSwitchDisabled,
  hasVisibleReceipt,
  isRowPending,
} from '../src/lib/acquisitionViewState.ts';

// ---------------------------------------------------------------------------
// A failed read must never be drawn as an empty one.
//
// Both acquisition pages derived their body from `rows.length === 0`, so every
// way of failing to *learn* the rows — a 500, a dropped connection, an expired
// session, losing the admin role mid-session — arrived on screen as a calm
// "No catalogs yet." / "Nothing requested yet.". An administrator reads that
// as "my catalog is gone" and adds it again; a user reads it as "my request
// vanished" and asks twice. Neither is told anything failed.
// ---------------------------------------------------------------------------

test('a read that failed with nothing behind it is an error, not an empty list', () => {
  const view = acquisitionSectionView({ isError: true, hasData: false, isEmpty: true });
  assert.equal(view.body, 'error');
  assert.equal(view.showError, true);
});

test('a failed read never shows the empty message even when it has an empty payload', () => {
  // A previous load legitimately returned zero rows, then a refetch failed.
  // "Nothing here" beside "we could not check" is a contradiction, so the
  // failure wins.
  const view = acquisitionSectionView({ isError: true, hasData: true, isEmpty: true });
  assert.equal(view.body, 'error');
  assert.equal(view.showError, true);
});

test('a refetch failure keeps rows the user already had, and still says so', () => {
  // Blanking a good list because a background refresh failed loses
  // information the user could act on; staying silent lets them act on rows
  // the server has stopped confirming. Keep the rows and show the message.
  const view = acquisitionSectionView({ isError: true, hasData: true, isEmpty: false });
  assert.equal(view.body, 'ready');
  assert.equal(view.showError, true);
});

test('the empty message needs the server to have actually said "nothing"', () => {
  const view = acquisitionSectionView({ hasData: true, isEmpty: true });
  assert.equal(view.body, 'empty');
  assert.equal(view.showError, false);
});

test('rows present render as ready with no error beside them', () => {
  const view = acquisitionSectionView({ hasData: true, isEmpty: false });
  assert.deepEqual(view, { body: 'ready', showError: false });
});

test('a load in flight is a load, not an empty list', () => {
  const view = acquisitionSectionView({ isLoading: true });
  assert.deepEqual(view, { body: 'loading', showError: false });
});

test('a query with no payload, no error and no loading flag keeps waiting', () => {
  // react-query parks a paused (offline) fetch here: status pending, nothing
  // fetching, data undefined. Claiming emptiness from that is the same bug in
  // a different dress, so absence of a payload can never produce 'empty'.
  const view = acquisitionSectionView({});
  assert.equal(view.body, 'loading');
  assert.notEqual(view.body, 'empty');
});

test('a query that was never asked is idle, and reports no failure', () => {
  // The grants and queue endpoints answer 404 while the feature is off, so the
  // page does not ask. That is not a failure to show the administrator.
  const view = acquisitionSectionView({ enabled: false, isError: true });
  assert.deepEqual(view, { body: 'idle', showError: false });
});

test('a known failure outranks the retry running behind it', () => {
  // Otherwise a retry loop hides the very failure it is retrying, and the
  // section flickers between a spinner and nothing at all.
  const view = acquisitionSectionView({ isError: true, isLoading: true, hasData: false });
  assert.equal(view.body, 'error');
  assert.equal(view.showError, true);
});

// ---------------------------------------------------------------------------
// The approval queue must not accuse a real account of being deleted.
//
// The queue and the grants list are two independent requests. The queue can
// answer first; the name map is then empty and every waiting request reads as
// "Requested by a removed account". The administrator is being asked to
// approve a book for someone the page has just told them does not exist.
// ---------------------------------------------------------------------------

test('a requester is not called removed while the name directory is still loading', () => {
  assert.deepEqual(
    acquisitionRequesterLabel({ ownerId: 7, names: new Map(), resolved: false }),
    { kind: 'unknown' },
  );
});

test('a requester is not called removed when the name directory failed', () => {
  // `resolved` is false for a failed grants query too — the page knows
  // nothing about who exists, which is not the same as knowing they do not.
  assert.deepEqual(
    acquisitionRequesterLabel({ ownerId: 7, names: undefined, resolved: false }),
    { kind: 'unknown' },
  );
});

test('a resolved directory names the requester', () => {
  assert.deepEqual(
    acquisitionRequesterLabel({ ownerId: 7, names: new Map([[7, 'maggie']]), resolved: true }),
    { kind: 'named', name: 'maggie' },
  );
});

test('only a resolved directory that lacks the account reports it removed', () => {
  assert.deepEqual(
    acquisitionRequesterLabel({ ownerId: 7, names: new Map([[9, 'someone']]), resolved: true }),
    { kind: 'removed' },
  );
});

test('an account that exists but has no display name is not reported as removed', () => {
  // The row is about a real person an administrator is about to judge.
  for (const blank of ['', '   ']) {
    assert.deepEqual(
      acquisitionRequesterLabel({ ownerId: 7, names: new Map([[7, blank]]), resolved: true }),
      { kind: 'unknown' },
      `a name of ${JSON.stringify(blank)} must not read as a deleted account`,
    );
  }
});

// ---------------------------------------------------------------------------
// Withdrawing a catalog is a safety control and must stay reachable.
//
// The server refuses only the *enable* direction while the migration is
// unresolved (`PATCH /admin/acquisition/connections/<id>` returns
// `needs_review` under `if body['enabled'] and migration_status != 'ready'`).
// The page disabled the switch in both directions, so an administrator whose
// database went to `needs_review` could not turn OFF a catalog that was
// already on — exactly when they might be trying to contain it.
// ---------------------------------------------------------------------------

test('the server only refuses switching a catalog on', () => {
  assert.equal(connectionEnableRejected(true, 'needs_review'), true);
  assert.equal(connectionEnableRejected(true, 'unavailable'), true);
  assert.equal(connectionEnableRejected(true, undefined), true);
  assert.equal(connectionEnableRejected(true, 'ready'), false);
  for (const migration of ['ready', 'needs_review', 'unavailable', undefined]) {
    assert.equal(connectionEnableRejected(false, migration), false,
      `switching off must be accepted with migration ${migration}`);
  }
});

test('an enabled catalog can always be switched off, whatever the migration says', () => {
  for (const migration of ['ready', 'needs_review', 'unavailable', undefined]) {
    assert.equal(
      connectionSwitchDisabled({ currentlyEnabled: true, migrationStatus: migration }),
      false,
      `withdrawing a catalog must stay available with migration ${migration}`,
    );
  }
});

test('a disabled catalog cannot be switched on until the migration is ready', () => {
  for (const migration of ['needs_review', 'unavailable', undefined]) {
    assert.equal(
      connectionSwitchDisabled({ currentlyEnabled: false, migrationStatus: migration }),
      true,
      `enabling must be blocked with migration ${migration}`,
    );
  }
  assert.equal(
    connectionSwitchDisabled({ currentlyEnabled: false, migrationStatus: 'ready' }),
    false,
  );
});

test('a write in flight on this catalog disables its own switch, in either direction', () => {
  assert.equal(
    connectionSwitchDisabled({ currentlyEnabled: true, migrationStatus: 'ready', pending: true }),
    true,
  );
  assert.equal(
    connectionSwitchDisabled({ currentlyEnabled: false, migrationStatus: 'ready', pending: true }),
    true,
  );
});

// ---------------------------------------------------------------------------
// One busy row must not disable the others.
// ---------------------------------------------------------------------------

test('only the row whose request is in flight is busy', () => {
  assert.equal(isRowPending(true, 'catalog-a', 'catalog-a'), true);
  assert.equal(isRowPending(true, 'catalog-a', 'catalog-b'), false);
});

test('nothing is busy when no request is in flight', () => {
  assert.equal(isRowPending(false, 'catalog-a', 'catalog-a'), false);
});

test('a pending mutation with no variables yet does not disable an arbitrary row', () => {
  // react-query exposes `variables` as undefined for a beat; matching
  // undefined against an undefined row id would disable the wrong control.
  assert.equal(isRowPending(true, undefined, 'catalog-a'), false);
  assert.equal(isRowPending<string | undefined>(true, undefined, undefined), false);
});

test('numeric row identities work, so grant rows keyed by account id are covered', () => {
  assert.equal(isRowPending(true, 3, 3), true);
  assert.equal(isRowPending(true, 3, 4), false);
});

// ---------------------------------------------------------------------------
// An import the requester cannot open is not "in your library".
// ---------------------------------------------------------------------------

test('a receipt carrying book ids is openable', () => {
  assert.equal(hasVisibleReceipt([12]), true);
});

test('an import with no ids this account may see is not claimed as openable', () => {
  // The job really did reach `imported`; the book is just not visible to the
  // requester. Saying "in your library" with nothing to open reads as a bug.
  assert.equal(hasVisibleReceipt([]), false);
  assert.equal(hasVisibleReceipt(undefined), false);
});
