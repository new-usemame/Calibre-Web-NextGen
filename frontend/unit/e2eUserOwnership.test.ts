import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  cleanupOwnedUser,
  createOwnedUser,
  createOwnedUserIdentity,
  reapOwnedE2EUsers,
  recordCreatedUser,
  registerOwnedUserIntent,
  type OwnedUserAdminApi,
  type OwnedUserCreate,
} from '../e2e/user-reaper.ts';

const BASE_URL = 'http://fixture.invalid:8086';
const HOST = 'unit-test-host';
const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map(
    (root) => rm(root, { recursive: true, force: true }),
  ));
});

async function registryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), 'cwng-e2e-ownership-test-'));
  temporaryRoots.push(root);
  return root;
}

async function recordFiles(root: string): Promise<string[]> {
  const instanceDirectories = await readdir(root, { withFileTypes: true });
  const files: string[] = [];
  for (const directory of instanceDirectories) {
    if (!directory.isDirectory()) continue;
    const directoryPath = path.join(root, directory.name);
    for (const entry of await readdir(directoryPath, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.endsWith('.json')) {
        files.push(path.join(directoryPath, entry.name));
      }
    }
  }
  return files;
}

type User = { id: number; name: string; email: string };

class FakeAdminApi implements OwnedUserAdminApi {
  readonly users: User[] = [];
  readonly deleted: number[] = [];
  deleteFailures = 0;
  beforeCreate?: () => Promise<void>;
  private nextId = 40;

  async createUser(input: OwnedUserCreate): Promise<User> {
    await this.beforeCreate?.();
    const user = { id: this.nextId, name: input.name, email: input.email };
    this.nextId += 1;
    this.users.push(user);
    return user;
  }

  async listUsers(): Promise<User[]> {
    return this.users.map((user) => ({ ...user }));
  }

  async deleteUser(userId: number): Promise<{ deleted: boolean; detail: string }> {
    if (this.deleteFailures > 0) {
      this.deleteFailures -= 1;
      return { deleted: false, detail: 'HTTP 503 try later' };
    }
    const index = this.users.findIndex((user) => user.id === userId);
    if (index >= 0) this.users.splice(index, 1);
    this.deleted.push(userId);
    return { deleted: true, detail: index >= 0 ? 'HTTP 204' : 'HTTP 404' };
  }
}

function createInput(name: string, email: string): OwnedUserCreate {
  return {
    name,
    email,
    password: 'Aa7!unit-test-password',
    roles: {
      admin: false,
      viewer: true,
      download: true,
      upload: false,
      edit: false,
      edit_shelfs: false,
      delete_books: false,
    },
  };
}

describe('secondary-user ownership lifecycle', () => {
  test('persists intent before create, binds the exact result, then cleans up directly', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const identity = createOwnedUserIdentity('desktop', 1);

    api.beforeCreate = async () => {
      const files = await recordFiles(root);
      assert.equal(files.length, 1, 'ownership intent must predate the create call');
      const intent = JSON.parse(await readFile(files[0], 'utf8')) as Record<string, unknown>;
      assert.equal(intent.state, 'intent');
      assert.equal(intent.username, identity.username);
      assert.equal(intent.userId, undefined);
    };

    const owned = await createOwnedUser(
      api,
      BASE_URL,
      createInput(identity.username, identity.email),
      { runId: 'run-create', workerId: 'desktop-1' },
      { registryRoot: root, currentHost: HOST, ownerPid: 101 },
    );

    const bound = JSON.parse(await readFile(owned.ownership.filePath, 'utf8')) as Record<string, unknown>;
    assert.equal(bound.state, 'created');
    assert.equal(bound.userId, owned.user.id);
    assert.equal(bound.runId, 'run-create');
    assert.equal(bound.workerId, 'desktop-1');

    assert.equal(await cleanupOwnedUser(api, owned.ownership, owned.user.id, {
      retryDelayMs: 0,
    }), true);
    assert.deepEqual(api.deleted, [owned.user.id]);
    await assert.rejects(readFile(owned.ownership.filePath), /ENOENT/);
  });

  test('a later setup reclaims an exact account after its owning worker exits', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const identity = createOwnedUserIdentity('desktop', 2);
    const owned = await createOwnedUser(
      api,
      BASE_URL,
      createInput(identity.username, identity.email),
      { runId: 'killed-run', workerId: 'desktop-2' },
      { registryRoot: root, currentHost: HOST, ownerPid: 102 },
    );

    const summary = await reapOwnedE2EUsers(api, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: () => false,
      retryDelayMs: 0,
    });

    assert.deepEqual(api.deleted, [owned.user.id]);
    assert.equal(summary.reclaimed, 1);
    await assert.rejects(readFile(owned.ownership.filePath), /ENOENT/);
  });

  test('persistent delete failure records deferred state and reports once without replacing a product failure', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const identity = createOwnedUserIdentity('desktop', 3);
    const owned = await createOwnedUser(
      api,
      BASE_URL,
      createInput(identity.username, identity.email),
      { runId: 'run-deferred', workerId: 'desktop-3' },
      { registryRoot: root, currentHost: HOST, ownerPid: 103 },
    );
    api.deleteFailures = 3;
    const warnings: string[] = [];
    const productFailure = new Error('product assertion failed');
    let observed: unknown;

    try {
      try {
        throw productFailure;
      } finally {
        await cleanupOwnedUser(api, owned.ownership, owned.user.id, {
          retryDelayMs: 0,
          warn: (message) => warnings.push(message),
        });
      }
    } catch (error) {
      observed = error;
    }

    assert.equal(observed, productFailure, 'cleanup must preserve the product assertion');
    assert.equal(warnings.length, 1, 'bounded retries produce one deferred-cleanup report');
    assert.match(warnings[0], /Deferred/);
    const deferred = JSON.parse(await readFile(owned.ownership.filePath, 'utf8')) as Record<string, unknown>;
    assert.equal(deferred.state, 'deferred');
    assert.equal(deferred.cleanupFailures, 1);
    assert.match(String(deferred.lastCleanupError), /HTTP 503/);
  });

  test('two concurrent owners never cross-clean: only the dead owner is reclaimed', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const deadIdentity = createOwnedUserIdentity('desktop', 4);
    const liveIdentity = createOwnedUserIdentity('mobile', 5);
    const dead = await createOwnedUser(
      api,
      BASE_URL,
      createInput(deadIdentity.username, deadIdentity.email),
      { runId: 'old-run', workerId: 'desktop-4' },
      { registryRoot: root, currentHost: HOST, ownerPid: 104 },
    );
    const live = await createOwnedUser(
      api,
      BASE_URL,
      createInput(liveIdentity.username, liveIdentity.email),
      { runId: 'current-run', workerId: 'mobile-5' },
      { registryRoot: root, currentHost: HOST, ownerPid: 105 },
    );

    const summary = await reapOwnedE2EUsers(api, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: (pid) => pid === 105,
      retryDelayMs: 0,
    });

    assert.deepEqual(api.deleted, [dead.user.id]);
    assert.equal(summary.reclaimed, 1);
    assert.equal(summary.live, 1);
    assert.ok(api.users.some((user) => user.id === live.user.id));
    assert.equal(JSON.parse(await readFile(live.ownership.filePath, 'utf8')).userId, live.user.id);
  });

  test('recovery refuses an id whose returned identity does not exactly match ownership', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const identity = createOwnedUserIdentity('desktop', 6);
    const handle = await registerOwnedUserIntent(BASE_URL, identity, {
      registryRoot: root,
      currentHost: HOST,
      ownerPid: 106,
      runId: 'mismatch-run',
      workerId: 'desktop-6',
    });
    await recordCreatedUser(handle, 77);
    api.users.push({ id: 77, name: identity.username, email: 'somebody-else@example.test' });

    const summary = await reapOwnedE2EUsers(api, BASE_URL, {
      registryRoot: root,
      currentHost: HOST,
      isProcessAlive: () => false,
      retryDelayMs: 0,
      warn: () => undefined,
    });

    assert.deepEqual(api.deleted, []);
    assert.equal(summary.deferred, 1);
    assert.equal(JSON.parse(await readFile(handle.filePath, 'utf8')).state, 'deferred');
  });

  test('normal cleanup refuses a caller id that is not the durable binding', async () => {
    const root = await registryRoot();
    const api = new FakeAdminApi();
    const identity = createOwnedUserIdentity('desktop', 7);
    const owned = await createOwnedUser(
      api,
      BASE_URL,
      createInput(identity.username, identity.email),
      { runId: 'bound-run', workerId: 'desktop-7' },
      { registryRoot: root, currentHost: HOST, ownerPid: 107 },
    );
    const warnings: string[] = [];

    assert.equal(await cleanupOwnedUser(api, owned.ownership, owned.user.id + 1, {
      retryDelayMs: 0,
      warn: (message) => warnings.push(message),
    }), false);

    assert.deepEqual(api.deleted, []);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /does not match bound ownership id/);
    assert.equal(JSON.parse(await readFile(owned.ownership.filePath, 'utf8')).state, 'deferred');
  });
});
