import assert from 'node:assert/strict';
import test from 'node:test';

import { taskText } from '../src/lib/taskText.ts';

test('a task message with a book link reads as its text', () => {
  assert.equal(
    taskText('Upload: File format EPUB added to <a href="/book/5">Tom &amp; Jerry</a>'),
    'Upload: File format EPUB added to Tom & Jerry',
  );
});

test('escaped user text is shown once-decoded, never re-parsed into markup', () => {
  assert.equal(
    taskText('E-mail: Registration Email for user: &lt;img src=x onerror=&#34;x()&#34;&gt;O&#39;Brien'),
    'E-mail: Registration Email for user: <img src=x onerror="x()">O\'Brien',
  );
  // A literal "&amp;lt;" is an escaped ampersand followed by "lt;", not a "<".
  assert.equal(taskText('Tom &amp;lt; Jerry'), 'Tom &lt; Jerry');
});

test('missing values render as empty text', () => {
  assert.equal(taskText(undefined), '');
  assert.equal(taskText(''), '');
});
