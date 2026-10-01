import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classicFallbackPath } from '../src/lib/classicFallback.ts';

test('gated acquisition tools never suggest a missing classic route, including subpath mounts', () => {
  assert.equal(classicFallbackPath('/app/find-books', ''), null);
  assert.equal(classicFallbackPath('/books/app/find-books/', '/books'), null);
  assert.equal(classicFallbackPath('/app.v2/app/admin/acquisition', '/app.v2'), null);
  assert.equal(classicFallbackPath('/books/app/book/42', '/books'), '/books/book/42');
});


test('classic links preserve a literal mount prefix without opening an external URL', () => {
  assert.equal(classicFallbackPath('/app.v2/app/book/42', '/app.v2'), '/app.v2/book/42');
  assert.equal(classicFallbackPath('/appXv2/app/book/42', '/app.v2'), '/app.v2/');
  assert.equal(classicFallbackPath('/app//evil.example', ''), null);
});
