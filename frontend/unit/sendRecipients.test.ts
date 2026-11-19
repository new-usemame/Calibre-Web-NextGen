import assert from 'node:assert/strict';
import test from 'node:test';

import { hasRecipients, splitAddresses, toggleRecipients } from '../src/lib/sendRecipients.ts';

/* #2296: an admin ticks another user's eReader in the send panel. The recipient
 * field stays the single record of who gets the book, so a tick must add that
 * user's addresses exactly once and an untick must remove only theirs. */
test('ticking a user adds all of their addresses after the ones already there', () => {
  assert.equal(
    toggleRecipients('me@kindle.com', ['bob@kindle.com', 'bob2@kobo.example'], true),
    'me@kindle.com, bob@kindle.com, bob2@kobo.example',
  );
  assert.equal(toggleRecipients('', ['bob@kindle.com'], true), 'bob@kindle.com');
});

test('ticking twice, or with the address already typed in another case, adds it once', () => {
  const once = toggleRecipients('me@kindle.com', ['bob@kindle.com'], true);
  assert.equal(toggleRecipients(once, ['bob@kindle.com'], true), once);
  assert.equal(toggleRecipients('BOB@kindle.com', ['bob@kindle.com'], true), 'bob@kindle.com');
});

test('unticking removes only that user and leaves typed addresses alone', () => {
  assert.equal(
    toggleRecipients(' me@kindle.com ,bob@kindle.com,, typed@x.com ', ['bob@kindle.com'], false),
    'me@kindle.com, typed@x.com',
  );
  assert.equal(toggleRecipients('bob@kindle.com', ['bob@kindle.com'], false), '');
});

test('a user reads as ticked only when every one of their addresses is in the field', () => {
  assert.equal(hasRecipients('me@kindle.com, Bob@Kindle.com', ['bob@kindle.com']), true);
  assert.equal(hasRecipients('bob@kindle.com', ['bob@kindle.com', 'bob2@kobo.example']), false);
  assert.equal(hasRecipients('bobby@kindle.com', ['bob@kindle.com']), false);
  assert.equal(hasRecipients('anything', []), false);
});

/* #1763: the saved e-reader value is one comma-separated string; the send panel
 * shows one tickbox per address in it. */
test('the saved value splits into one trimmed address each, blanks dropped', () => {
  assert.deepEqual(splitAddresses(' a@kindle.com ,b@kindle.com,, c@kindle.com '), ['a@kindle.com', 'b@kindle.com', 'c@kindle.com']);
  assert.deepEqual(splitAddresses('only@kindle.com'), ['only@kindle.com']);
  assert.deepEqual(splitAddresses(''), []);
});

test('a repeated saved address gets one tickbox, whatever its case', () => {
  assert.deepEqual(splitAddresses('a@kindle.com, A@Kindle.com, b@kindle.com'), ['a@kindle.com', 'b@kindle.com']);
});

test('unticking one saved address leaves the others and ticking it back restores it', () => {
  const saved = 'a@kindle.com, b@kindle.com, c@kindle.com';
  const withoutB = toggleRecipients(saved, ['b@kindle.com'], false);
  assert.equal(withoutB, 'a@kindle.com, c@kindle.com');
  assert.equal(hasRecipients(withoutB, ['b@kindle.com']), false);
  assert.equal(hasRecipients(withoutB, ['a@kindle.com']), true);
  assert.equal(hasRecipients(toggleRecipients(withoutB, ['b@kindle.com'], true), ['b@kindle.com']), true);
});
