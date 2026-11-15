import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { apiGet } from '../src/lib/api.ts';

// Discover cancels old picks before saving a new source. The HTTP request must
// end too, so a delayed old response cannot repopulate the source's cache.
test('cancelling a GET ends its outstanding HTTP request', { timeout: 5000 }, async () => {
  let received: () => void = () => {};
  const requestStarted = new Promise<void>(resolve => { received = resolve; });
  let closed: () => void = () => {};
  const requestClosed = new Promise<void>(resolve => { closed = resolve; });
  const server = createServer((request, response) => {
    const timer = setTimeout(() => { response.setHeader('Content-Type', 'application/json'); response.end('{}'); }, 500);
    request.on('close', () => { clearTimeout(timer); closed(); });
    received();
    // Hold a delayed response long enough to observe cancellation.
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address !== 'string');
  const controller = new AbortController();
  try {
    const result = apiGet(`http://127.0.0.1:${address.port}/picks`, { signal: controller.signal });
    const rejected = assert.rejects(result, { name: 'AbortError' });
    await requestStarted;
    controller.abort();
    await rejected;
    await requestClosed;
  } finally {
    controller.abort();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
