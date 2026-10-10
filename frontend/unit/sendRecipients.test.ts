import assert from 'node:assert/strict';
import test from 'node:test';

import { hasRecipients, toggleRecipients } from '../src/lib/sendRecipients.ts';

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
