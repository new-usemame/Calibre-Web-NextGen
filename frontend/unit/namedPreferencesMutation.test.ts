import assert from 'node:assert/strict';
import test from 'node:test';
import { MutationObserver, QueryClient } from '@tanstack/react-query';
import type { Me } from '../src/lib/api.ts';
import { replaceCachedIdentity } from '../src/lib/identityCache.ts';
import { captureNamedPreferencesOwner, namedPreferencesMutationOptions } from '../src/lib/namedPreferencesMutation.ts';
const account = (id: number) => ({ id, name: `reader-${id}`,
  preferences: { share_book_ratings: false, show_library_rating: true } }) as unknown as Me;
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
test('delayed preference success, failure and queued work cannot cross an identity generation', async () => {
  for (const outcome of ['success', 'failure', 'same-account-new-session'] as const) {
    const client = new QueryClient(); client.setQueryData(['me'], account(1));
    const response = deferred<{ preferences: Record<string, boolean> }>();
    const dispatched = deferred<void>(); let requests = 0;
    const options = namedPreferencesMutationOptions(client, async () => {
      requests++; dispatched.resolve(); return response.promise;
    });
    const owner = captureNamedPreferencesOwner(client);
    const pending = new MutationObserver(client, options).mutate({ ...owner, preferences: { share_book_ratings: true } });
    const settled = pending.catch(error => error);
    await dispatched.promise;
    assert.equal(client.getQueryData<Me>(['me'])?.preferences?.share_book_ratings, true);
    const queued = new MutationObserver(client, options).mutate({ ...owner, preferences: { show_library_rating: false } });
    const queuedSettled = queued.catch(error => error);
    await replaceCachedIdentity(client, account(2));
    if (outcome === 'same-account-new-session') await replaceCachedIdentity(client, account(1));
    const expected = client.getQueryData(['me']);
    if (outcome === 'failure') response.reject(new Error('delayed network failure'));
    else response.resolve({ preferences: { share_book_ratings: true, show_library_rating: false } });
    await settled;
    assert.equal(await queuedSettled instanceof Error, true);
    assert.deepEqual(client.getQueryData(['me']), expected);
    assert.equal(requests, 1, 'queued previous-owner update must never reach transport');
    client.clear();
  }
});
