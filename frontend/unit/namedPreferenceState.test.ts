import assert from 'node:assert/strict';
import test from 'node:test';

import {
  queueNamedPreferenceAdoption,
  resolveNamedPreferenceState,
} from '../src/lib/namedPreferenceState.ts';

test('an authenticated server boolean is authoritative on the first render', () => {
  assert.deepEqual(
    resolveNamedPreferenceState(
      { role: { anonymous: false }, preferences: { discover_hidden: false } },
      'discover_hidden', true, true,
    ),
    {
      isGuest: false,
      hasServerSlot: true,
      serverValue: false,
      value: false,
      canPersist: true,
      shouldAdopt: false,
    },
  );
});

test('an unset account adopts an explicit existing local value', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: false }, preferences: { show_hidden_books: null } },
    'show_hidden_books', true, true,
  );
  assert.equal(state.value, true);
  assert.equal(state.canPersist, true);
  assert.equal(state.shouldAdopt, true);
});

test('an absent local key does not adopt the fallback default', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: false }, preferences: { card_actions_hidden: null } },
    'card_actions_hidden', false, null,
  );
  assert.equal(state.value, false);
  assert.equal(state.canPersist, true);
  assert.equal(state.shouldAdopt, false);
});

test('a guest stays local-only even if /me exposes preference slots', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: true }, preferences: { discover_hidden: null } },
    'discover_hidden', true, true,
  );
  assert.equal(state.value, true);
  assert.equal(state.canPersist, false);
  assert.equal(state.shouldAdopt, false);
});

test('loading and older-server states stay local and never post', () => {
  for (const me of [undefined, { role: { anonymous: false } }]) {
    const state = resolveNamedPreferenceState(
      me, 'discover_hidden', true, true,
    );
    assert.equal(state.value, true);
    assert.equal(state.canPersist, false);
    assert.equal(state.shouldAdopt, false);
  }
});

test('same-account adoptions coalesce into one write and fan errors out', async () => {
  const account = {};
  const writes: Array<{
    preferences: Record<string, boolean>;
    fail: () => void;
  }> = [];
  const errors: string[] = [];
  const mutate = (
    preferences: Record<string, boolean>,
    options: { onError: () => void },
  ) => writes.push({ preferences, fail: options.onError });

  queueNamedPreferenceAdoption(
    account, 'show_hidden_books', true, mutate, () => errors.push('hidden'));
  queueNamedPreferenceAdoption(
    account, 'card_actions_hidden', true, mutate, () => errors.push('cards'));
  await Promise.resolve();

  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].preferences, {
    show_hidden_books: true,
    card_actions_hidden: true,
  });
  writes[0].fail();
  assert.deepEqual(errors, ['hidden', 'cards']);
});

test('different accounts never share an adoption batch', async () => {
  const writes: Record<string, boolean>[] = [];
  const mutate = (
    preferences: Record<string, boolean>,
    _options: { onError: () => void },
  ) => writes.push(preferences);

  queueNamedPreferenceAdoption({}, 'discover_hidden', true, mutate);
  queueNamedPreferenceAdoption({}, 'discover_hidden', false, mutate);
  await Promise.resolve();

  assert.deepEqual(writes, [
    { discover_hidden: true },
    { discover_hidden: false },
  ]);
});
