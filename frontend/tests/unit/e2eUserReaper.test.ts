import { afterEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  cleanupOwnedUser,
  createOwnedUserIdentity,
  reapOwnedE2EUsers,
  recordCreatedUser,
  registerOwnedUserIntent,
  type OwnedUserAdminApi,
} from '../../e2e/user-reaper.ts';

const BASE_URL = 'http://fixture.invalid:8086';
const HOST = 'unit-test-host';
const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

async function registryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), 'cwng-e2e-reaper-test-'));
  temporaryRoots.push(root);
  return root;
}

function fakeAdmin(users: Array<{ id: number; name: string; email: string }>, failures = 0) {
  const deleted: number[] = [];
  let remainingFailures = failures;
  const request: OwnedUserAdminApi = {
    createUser: async () => {
      throw new Error('unexpected create');
    },
    listUsers: async () => users,
    deleteUser: async (userId: number) => {
      if (remainingFailures > 0) {
        remainingFailures -= 1;
        return { deleted: false, detail: 'HTTP 503 try later' };
      }
      deleted.push(userId);
      return { deleted: true, detail: 'HTTP 204' };
    },
  };
  return { request, deleted };
}

describe('killed-run ownership and liveness predicate', () => {
  test('reclaims a registered stale account and no unregistered or protected account', async () => {
    const root = await registryRoot();
    const stale = createOwnedUserIdentity('desktop', 2);
    const handle = await registerOwnedUserIntent(BASE_URL, stale, {
      registryRoot: root,
      currentHost: HOST,
    });
    await recordCreatedUser(handle, 44);
    const { request, deleted } = fakeAdmin([
      { id: 1, name: 'admin', email: 'admin@example.org' },
      { id: 2, name: 'Guest', email: '' },
      { id: 3, name: 'cwng84test', email: 'cwng84test@example.test' },
      { id: 43, name: 'e2e-v2-unregistered-0-aaaaaaaaaaaaaaaaaaaa', email: 'lookalike@example.test' },
      { id: 44, name: stale.username, email: stale.email },
    ]);

    const summary = await reapOwnedE2EUsers(request, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: () => false,
      retryDelayMs: 0,
    });

    assert.deepEqual(deleted, [44]);
    assert.equal(summary.reclaimed, 1);
    assert.equal(summary.live, 0);
  });

  test('does not issue a delete for a peer whose owner process is alive', async () => {
    const root = await registryRoot();
    const peer = createOwnedUserIdentity('desktop', 7);
    const handle = await registerOwnedUserIntent(BASE_URL, peer, {
      registryRoot: root,
      currentHost: HOST,
    });
    await recordCreatedUser(handle, 71);
    const { request, deleted } = fakeAdmin([
      { id: 71, name: peer.username, email: peer.email },
    ]);

    const summary = await reapOwnedE2EUsers(request, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: (pid) => pid === process.pid,
      retryDelayMs: 0,
    });

    assert.deepEqual(deleted, [], 'a live peer must be structurally outside the deletion set');
    assert.equal(summary.live, 1);
    assert.equal(summary.reclaimed, 0);
  });

  test('keeps an unconfirmed intent when the create response may still be committing', async () => {
    const root = await registryRoot();
    const identity = createOwnedUserIdentity('desktop', 8);
    const handle = await registerOwnedUserIntent(BASE_URL, identity, {
      registryRoot: root,
      currentHost: HOST,
    });
    const { request, deleted } = fakeAdmin([]);

    const summary = await reapOwnedE2EUsers(request, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: () => false,
      retryDelayMs: 0,
    });

    assert.deepEqual(deleted, []);
    assert.equal(summary.deferred, 1);
    assert.equal(JSON.parse(await readFile(handle.filePath, 'utf8')).username, identity.username);
  });

  test('normal teardown deletes directly and removes the durable record', async () => {
    const root = await registryRoot();
    const identity = createOwnedUserIdentity('desktop', 1);
    const handle = await registerOwnedUserIntent(BASE_URL, identity, {
      registryRoot: root,
      currentHost: HOST,
    });
    await recordCreatedUser(handle, 88);
    const { request, deleted } = fakeAdmin([]);

    assert.equal(await cleanupOwnedUser(request, handle, 88, { retryDelayMs: 0 }), true);
    assert.deepEqual(deleted, [88]);
    await assert.rejects(readFile(handle.filePath), /ENOENT/);
  });

  test('persistent delete failure is warned and durably deferred, not thrown or forgotten', async () => {
    const root = await registryRoot();
    const identity = createOwnedUserIdentity('desktop', 3);
    const handle = await registerOwnedUserIntent(BASE_URL, identity, {
      registryRoot: root,
      currentHost: HOST,
    });
    await recordCreatedUser(handle, 99);
    const { request, deleted } = fakeAdmin([], 3);
    const warnings: string[] = [];

    assert.equal(await cleanupOwnedUser(request, handle, 99, {
      retryDelayMs: 0,
      warn: (message) => warnings.push(message),
    }), false);
    assert.deepEqual(deleted, []);
    assert.match(warnings.join('\n'), /Deferred .* durable ownership record retained/);
    assert.equal((await readdir(path.dirname(handle.filePath))).filter((name) => name.endsWith('.json')).length, 1);
  });
});
