import { test } from 'node:test';
import assert from 'node:assert/strict';

// #571: behind a reverse proxy mounted at a subpath (https://host/cwa/), every
// request the new UI makes must carry the mount prefix, or it 404s and the page
// is blank. The server injects window.__CWNG_PREFIX__ into the shell; api.ts
// reads it once at import, so the window has to exist before the import.
//
// The trailing slash is deliberate: a proxy sending X-Forwarded-Prefix: /cwa/
// must not produce //api/v1 URLs.
const fetched: string[] = [];
const g = globalThis as unknown as Record<string, unknown>;
g.window = { __CWNG_PREFIX__: '/cwa/', location: { pathname: '/cwa/app/books', href: 'http://host/cwa/app/books' } };
g.document = { cookie: '' };
g.fetch = async (input: unknown) => {
  fetched.push(String(input));
  return new Response(JSON.stringify({ csrf_token: 't' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};

const api = await import('../../src/lib/api.ts');

test('the injected mount prefix is used, without a trailing slash', () => {
  assert.equal(api.BASE_PREFIX, '/cwa');
  assert.equal(api.apiUrl('/api/v1/books'), '/cwa/api/v1/books');
});

test('every request helper sends its request under the mount prefix', async () => {
  const calls: [string, () => Promise<unknown>][] = [
    // First, while no CSRF token is cached yet, so it really fetches one.
    ['getCsrf', () => api.getCsrf()],
    ['apiGet', () => api.apiGet('/api/v1/books')],
    ['apiPost', () => api.apiPost('/api/v1/shelves', { name: 'x' })],
    ['apiPut', () => api.apiPut('/api/v1/books/1/status', { status: 'finished' })],
    ['apiPatch', () => api.apiPatch('/api/v1/books/1', { title: 'x' })],
    ['apiDelete', () => api.apiDelete('/api/v1/shelves/1')],
    ['apiPostForm', () => api.apiPostForm('/admin/x', { a: '1' })],
    ['apiUpload', () => api.apiUpload('/api/v1/upload', new FormData())],
  ];
  for (const [name, call] of calls) {
    fetched.length = 0;
    await call().catch(() => undefined);
    assert.ok(fetched.length > 0, `${name} made no request`);
    for (const url of fetched) {
      assert.ok(url.startsWith('/cwa/'), `${name} requested ${url} without the /cwa prefix`);
      assert.ok(!url.startsWith('/cwa/cwa/'), `${name} requested ${url} with the prefix twice`);
    }
  }
});

test('server resource URLs gain the prefix exactly once', () => {
  assert.equal(api.resourceUrl('/cover/7'), '/cwa/cover/7');
  // An apply endpoint can return a url_for path that already carries the prefix.
  assert.equal(api.resourceUrl('/cwa/cover/7'), '/cwa/cover/7');
  assert.equal(api.resourceUrl('/cwa'), '/cwa');
  // A sibling path that merely starts with the same letters is not the prefix.
  assert.equal(api.resourceUrl('/cwabooks/cover/7'), '/cwa/cwabooks/cover/7');
  // External provider covers and inline images are left alone.
  assert.equal(api.resourceUrl('https://covers.example/x.jpg'), 'https://covers.example/x.jpg');
  assert.equal(api.resourceUrl('//covers.example/x.jpg'), '//covers.example/x.jpg');
  assert.equal(api.resourceUrl('data:image/png;base64,AA'), 'data:image/png;base64,AA');
});
