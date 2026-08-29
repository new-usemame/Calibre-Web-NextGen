import type { APIRequestContext } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import { homedir, hostname } from 'node:os';
import path from 'node:path';

const RECORD_VERSION = 1;
const RECORD_MARKER = 'cwng-playwright-secondary-user';
const USERNAME_PATTERN = /^e2e-v2-[a-z0-9][a-z0-9-]{0,11}-\d+-[a-f0-9]{20}$/;
const DEFAULT_REGISTRY_ROOT = path.join(
  homedir(),
  '.cache',
  'cwng',
  'e2e-user-ownership-v1',
);
const REQUEST_TIMEOUT_MS = 5_000;
const DELETE_ATTEMPTS = 3;

type AdminRequest = Pick<APIRequestContext, 'get' | 'post'>;

export interface OwnedUserIdentity {
  username: string;
  email: string;
}

interface OwnershipRecord extends OwnedUserIdentity {
  version: typeof RECORD_VERSION;
  marker: typeof RECORD_MARKER;
  recordId: string;
  baseURL: string;
  ownerPid: number;
  ownerHost: string;
  createdAt: string;
  userId?: number;
}

export interface OwnershipHandle {
  record: OwnershipRecord;
  filePath: string;
}

interface AdminUser {
  id: number;
  name: string;
  email: string;
}

export interface ReaperSummary {
  inspected: number;
  live: number;
  reclaimed: number;
  absent: number;
  deferred: number;
}

interface RegistryOptions {
  registryRoot?: string;
  currentHost?: string;
  isProcessAlive?: (pid: number) => boolean;
  warn?: (message: string) => void;
  retryDelayMs?: number;
}

function normalizedBaseURL(baseURL: string): string {
  const url = new URL(baseURL);
  const pathname = url.pathname.replace(/\/+$/, '');
  return `${url.origin}${pathname}`;
}

function instanceDirectory(baseURL: string, registryRoot = DEFAULT_REGISTRY_ROOT): string {
  const instanceKey = createHash('sha256')
    .update(normalizedBaseURL(baseURL))
    .digest('hex')
    .slice(0, 24);
  return path.join(registryRoot, instanceKey);
}

function assertSuiteUsername(username: string): void {
  if (!USERNAME_PATTERN.test(username)) {
    throw new Error(`Refusing non-CWNG-E2E ownership name: ${username}`);
  }
}

export function createOwnedUserIdentity(projectName: string, workerIndex: number): OwnedUserIdentity {
  const project = projectName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 12) || 'project';
  const suffix = randomUUID().replaceAll('-', '').slice(0, 20);
  const username = `e2e-v2-${project}-${workerIndex}-${suffix}`;
  assertSuiteUsername(username);
  return { username, email: `${username}@example.test` };
}

async function writeRecord(handle: OwnershipHandle): Promise<void> {
  const temporaryPath = `${handle.filePath}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temporaryPath, `${JSON.stringify(handle.record)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  });
  await fs.rename(temporaryPath, handle.filePath);
}

/**
 * Persist intent before the create request. If the worker is killed after the
 * server commits but before the response is decoded, the next setup run can
 * still find the account by its exact random username + email pair.
 */
export async function registerOwnedUserIntent(
  baseURL: string,
  identity: OwnedUserIdentity,
  options: RegistryOptions = {},
): Promise<OwnershipHandle> {
  assertSuiteUsername(identity.username);
  if (identity.email !== `${identity.username}@example.test`) {
    throw new Error('Refusing an ownership record without the E2E email binding');
  }

  const directory = instanceDirectory(baseURL, options.registryRoot);
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await fs.chmod(directory, 0o700);
  const recordId = randomUUID();
  const handle: OwnershipHandle = {
    record: {
      version: RECORD_VERSION,
      marker: RECORD_MARKER,
      recordId,
      baseURL: normalizedBaseURL(baseURL),
      username: identity.username,
      email: identity.email,
      ownerPid: process.pid,
      ownerHost: options.currentHost ?? hostname(),
      createdAt: new Date().toISOString(),
    },
    filePath: path.join(directory, `${recordId}.json`),
  };
  await writeRecord(handle);
  return handle;
}

export async function recordCreatedUser(handle: OwnershipHandle, userId: number): Promise<void> {
  handle.record.userId = userId;
  await writeRecord(handle);
}

async function forgetRecord(handle: OwnershipHandle): Promise<void> {
  await fs.unlink(handle.filePath).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
  });
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

export function ownerIsLive(
  record: Pick<OwnershipRecord, 'ownerHost' | 'ownerPid'>,
  options: RegistryOptions = {},
): boolean {
  const currentHost = options.currentHost ?? hostname();
  // A PID is meaningful only on its own host. Conservatively protecting a
  // foreign-host record may defer a cleanup; it can never delete a live peer.
  if (record.ownerHost !== currentHost) return true;
  return (options.isProcessAlive ?? processIsAlive)(record.ownerPid);
}

async function csrfToken(request: AdminRequest): Promise<string> {
  const response = await request.get('/api/v1/auth/csrf', { timeout: REQUEST_TIMEOUT_MS });
  if (!response.ok()) {
    throw new Error(`CSRF endpoint returned HTTP ${response.status()}: ${await response.text()}`);
  }
  const payload = (await response.json()) as { csrf_token?: unknown };
  if (typeof payload.csrf_token !== 'string' || !payload.csrf_token) {
    throw new Error('CSRF endpoint returned no csrf_token');
  }
  return payload.csrf_token;
}

async function deleteUserWithRetry(
  request: AdminRequest,
  userId: number,
  retryDelayMs: number,
): Promise<{ deleted: boolean; detail: string }> {
  let detail = 'delete was not attempted';
  for (let attempt = 1; attempt <= DELETE_ATTEMPTS; attempt += 1) {
    try {
      const response = await request.post(`/api/v1/admin/users/${userId}/delete`, {
        headers: { 'X-CSRFToken': await csrfToken(request) },
        timeout: REQUEST_TIMEOUT_MS,
      });
      if (response.status() === 204 || response.status() === 404) {
        return { deleted: true, detail: `HTTP ${response.status()}` };
      }
      detail = `attempt ${attempt}/${DELETE_ATTEMPTS}: HTTP ${response.status()} ${await response.text()}`;
    } catch (error) {
      detail = `attempt ${attempt}/${DELETE_ATTEMPTS}: ${String(error)}`;
    }
    if (attempt < DELETE_ATTEMPTS && retryDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
  }
  return { deleted: false, detail };
}

/** Normal fixture teardown: delete immediately, then forget ownership. */
export async function cleanupOwnedUser(
  request: AdminRequest,
  handle: OwnershipHandle,
  userId: number,
  options: RegistryOptions = {},
): Promise<boolean> {
  const warn = options.warn ?? console.warn;
  const result = await deleteUserWithRetry(request, userId, options.retryDelayMs ?? 150);
  if (!result.deleted) {
    warn(
      `[e2e-user-cleanup] Deferred ${handle.record.username}; durable ownership record retained for the next setup run (${result.detail})`,
    );
    return false;
  }
  try {
    await forgetRecord(handle);
  } catch (error) {
    warn(
      `[e2e-user-cleanup] Deleted ${handle.record.username}, but could not remove its ownership record; the next setup run will reconcile it (${String(error)})`,
    );
  }
  return true;
}

function isOwnershipRecord(value: unknown, expectedBaseURL: string): value is OwnershipRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<OwnershipRecord>;
  return record.version === RECORD_VERSION
    && record.marker === RECORD_MARKER
    && typeof record.recordId === 'string'
    && record.baseURL === expectedBaseURL
    && typeof record.ownerPid === 'number'
    && Number.isSafeInteger(record.ownerPid)
    && record.ownerPid > 0
    && typeof record.ownerHost === 'string'
    && typeof record.username === 'string'
    && USERNAME_PATTERN.test(record.username)
    && record.email === `${record.username}@example.test`
    && (record.userId === undefined
      || (typeof record.userId === 'number' && Number.isSafeInteger(record.userId) && record.userId > 0));
}

async function readOwnershipHandles(
  baseURL: string,
  options: RegistryOptions,
): Promise<OwnershipHandle[]> {
  const directory = instanceDirectory(baseURL, options.registryRoot);
  const entries = await fs.readdir(directory, { withFileTypes: true }).catch(
    (error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return [];
      throw error;
    },
  );
  const expectedBaseURL = normalizedBaseURL(baseURL);
  const handles: OwnershipHandle[] = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
    const filePath = path.join(directory, entry.name);
    try {
      const record = JSON.parse(await fs.readFile(filePath, 'utf8')) as unknown;
      if (!isOwnershipRecord(record, expectedBaseURL)) {
        options.warn?.(`[e2e-user-reaper] Ignoring invalid ownership record ${filePath}`);
        continue;
      }
      handles.push({ record, filePath });
    } catch (error) {
      options.warn?.(`[e2e-user-reaper] Ignoring unreadable ownership record ${filePath}: ${String(error)}`);
    }
  }
  return handles;
}

/**
 * Reclaim accounts from workers that no longer exist. Deletion candidates are
 * the intersection of a suite-written ownership record, a dead local PID, and
 * an exact username/email/(when known) id match returned by this same server.
 */
export async function reapOwnedE2EUsers(
  request: AdminRequest,
  baseURL: string,
  options: RegistryOptions = {},
): Promise<ReaperSummary> {
  const warn = options.warn ?? console.warn;
  const handles = await readOwnershipHandles(baseURL, { ...options, warn });
  const summary: ReaperSummary = {
    inspected: handles.length,
    live: 0,
    reclaimed: 0,
    absent: 0,
    deferred: 0,
  };
  const stale = handles.filter((handle) => {
    if (ownerIsLive(handle.record, options)) {
      summary.live += 1;
      return false;
    }
    return true;
  });
  if (stale.length === 0) return summary;

  let users: AdminUser[];
  try {
    const response = await request.get('/api/v1/admin/users', { timeout: REQUEST_TIMEOUT_MS });
    if (!response.ok()) {
      throw new Error(`HTTP ${response.status()}: ${await response.text()}`);
    }
    const payload = (await response.json()) as { items?: unknown };
    if (!Array.isArray(payload.items)) throw new Error('response has no items array');
    users = payload.items as AdminUser[];
  } catch (error) {
    summary.deferred = stale.length;
    warn(`[e2e-user-reaper] Could not list owned users; ${stale.length} cleanup(s) deferred: ${String(error)}`);
    return summary;
  }

  for (const handle of stale) {
    const { record } = handle;
    const exact = users.find((user) => (
      user.name === record.username
      && user.email === record.email
      && (record.userId === undefined || user.id === record.userId)
    ));
    if (!exact) {
      // With a confirmed id, absence means normal teardown won its race with
      // this reaper (or an operator already removed the exact account). An
      // intent without an id is different: the worker may have died while the
      // POST was still committing. Retain it so a later run can see that late
      // commit instead of turning it into an untracked leak.
      if (record.userId === undefined) {
        summary.deferred += 1;
      } else {
        await forgetRecord(handle).catch((error) => {
          warn(`[e2e-user-reaper] Could not reconcile absent ${record.username}: ${String(error)}`);
        });
        summary.absent += 1;
      }
      continue;
    }

    const result = await deleteUserWithRetry(request, exact.id, options.retryDelayMs ?? 150);
    if (!result.deleted) {
      summary.deferred += 1;
      warn(
        `[e2e-user-reaper] Deferred stale ${record.username}; durable ownership record retained (${result.detail})`,
      );
      continue;
    }
    try {
      await forgetRecord(handle);
      summary.reclaimed += 1;
    } catch (error) {
      summary.deferred += 1;
      warn(`[e2e-user-reaper] Reclaimed ${record.username}, but record reconciliation was deferred: ${String(error)}`);
    }
  }
  return summary;
}
