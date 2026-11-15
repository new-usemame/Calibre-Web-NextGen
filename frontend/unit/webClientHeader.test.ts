import assert from 'node:assert/strict';
import { createServer, type IncomingHttpHeaders } from 'node:http';
import test from 'node:test';
import { apiGet } from '../src/lib/api.ts';

// #2844: the server reads /api/v1 as a native client's HTTP Basic app password
// unless the request says it is the new UI. Behind a proxy with its own Basic
// login over plain HTTP (no Sec-Fetch-Site), a missing marker turns a guest's
// reads into 401s, and a 401 on /auth/me reads to the UI as a lost session.
test('new-UI fetches mark themselves as the web client', { timeout: 5000 }, async () => {
  const seen: IncomingHttpHeaders[] = [];
  const server = createServer((request, response) => {
    seen.push(request.headers);
    response.setHeader('Content-Type', 'application/json');
    response.end('{}');
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address !== 'string');
  const base = `http://127.0.0.1:${address.port}`;
  try {
    await apiGet(`${base}/api/v1/shelves`);
    assert.equal(seen[seen.length - 1]?.['x-cwng-client'], 'web');
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
