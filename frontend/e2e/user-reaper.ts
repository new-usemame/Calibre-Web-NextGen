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
const DELETE_ATTEMPTS = 3;

export interface OwnedUserIdentity {
  username: string;
  email: string;
}

type OwnershipState = 'intent' | 'created' | 'deferred';

interface OwnershipRecord extends OwnedUserIdentity {
  version: typeof RECORD_VERSION;
  marker: typeof RECORD_MARKER;
  recordId: string;
  baseURL: string;
  ownerPid: number;
  ownerHost: string;
  createdAt: string;
  /** Added after v1 shipped; absent fields identify a recoverable legacy record. */
  runId?: string;
  workerId?: string;
  state?: OwnershipState;
  userId?: number;
  cleanupFailures?: number;
  lastCleanupAttemptAt?: string;
  lastCleanupError?: string;
  lastReportedAt?: string;
}

export interface OwnershipHandle {
  record: OwnershipRecord;
  filePath: string;
}

export interface AdminUser {
  id: number;
  name: string;
  email: string;
}

export interface OwnedUserCreate {
  name: string;
  email: string;
  password: string;
  roles: Record<string, boolean>;
}

/**
 * Page-free boundary used by both fixture teardown and next-run recovery.
 * The production adapter owns an isolated authenticated API session; tests use
 * an in-memory fake so no shared instance is touched.
 */
export interface OwnedUserAdminApi {
  createUser(input: OwnedUserCreate): Promise<AdminUser>;
  listUsers(): Promise<AdminUser[]>;
  deleteUser(userId: number): Promise<{ deleted: boolean; detail: string }>;
}

export interface OwnershipOwner {
  runId: string;
  workerId: string;
}

export interface CreatedOwnedUser {
  ownership: OwnershipHandle;
  user: AdminUser;
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
  ownerPid?: number;
  runId?: string;
  workerId?: string;
  now?: () => Date;
  intentGraceMs?: number;
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

function ownershipKey(runId: string, workerId: string): string {
  return createHash('sha256')
    .update(`${runId}\0${workerId}`)
    .digest('hex')
    .slice(0, 20);
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
  const suffix = randomUUID().replace(/-/g, '').slice(0, 20);
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
  const runId = options.runId ?? `process-${process.pid}`;
  const workerId = options.workerId ?? 'unknown-worker';
  const ownerKey = ownershipKey(runId, workerId);
  const handle: OwnershipHandle = {
    record: {
      version: RECORD_VERSION,
      marker: RECORD_MARKER,
      recordId,
      baseURL: normalizedBaseURL(baseURL),
      username: identity.username,
      email: identity.email,
      ownerPid: options.ownerPid ?? process.pid,
      ownerHost: options.currentHost ?? hostname(),
      createdAt: (options.now?.() ?? new Date()).toISOString(),
      runId,
      workerId,
      state: 'intent',
    },
    // The filename makes run/worker ownership visible without trusting JSON
    // contents. recordId still permits multiple lazy fixtures in one worker.
    filePath: path.join(directory, `${ownerKey}-${recordId}.json`),
  };
  await writeRecord(handle);
  return handle;
}

export async function recordCreatedUser(handle: OwnershipHandle, userId: number): Promise<void> {
  if (!Number.isSafeInteger(userId) || userId <= 0) {
    throw new Error(`Refusing invalid owned user id: ${userId}`);
  }
  handle.record.userId = userId;
  handle.record.state = 'created';
  await writeRecord(handle);
}

/** Persist intent, create through the direct API, then bind its exact identity. */
export async function createOwnedUser(
  api: OwnedUserAdminApi,
  baseURL: string,
  input: OwnedUserCreate,
  owner: OwnershipOwner,
  options: RegistryOptions = {},
): Promise<CreatedOwnedUser> {
  const ownership = await registerOwnedUserIntent(baseURL, {
    username: input.name,
    email: input.email,
  }, {
    ...options,
    runId: owner.runId,
    workerId: owner.workerId,
  });
  const user = await api.createUser(input);
  if (!Number.isSafeInteger(user.id) || user.id <= 0
      || user.name !== input.name || user.email !== input.email) {
    // The intent deliberately remains unbound. Recovery may safely locate only
    // the exact requested username/email if the server committed unexpectedly.
    throw new Error(
      `Created secondary user identity mismatch: requested ${input.name}/${input.email}, `
      + `received ${String(user.id)}/${user.name}/${user.email}`,
    );
  }
  await recordCreatedUser(ownership, user.id);
  return { ownership, user };
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

async function deleteUserWithRetry(
  api: OwnedUserAdminApi,
  userId: number,
  retryDelayMs: number,
): Promise<{ deleted: boolean; detail: string }> {
  let detail = 'delete was not attempted';
  for (let attempt = 1; attempt <= DELETE_ATTEMPTS; attempt += 1) {
    try {
      const result = await api.deleteUser(userId);
      if (result.deleted) return result;
      detail = `attempt ${attempt}/${DELETE_ATTEMPTS}: ${result.detail}`;
    } catch (error) {
      detail = `attempt ${attempt}/${DELETE_ATTEMPTS}: ${String(error)}`;
    }
    if (attempt < DELETE_ATTEMPTS && retryDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
  }
  return { deleted: false, detail };
}

async function retainDeferredCleanup(
  handle: OwnershipHandle,
  detail: string,
  options: RegistryOptions,
): Promise<void> {
  const now = (options.now?.() ?? new Date()).toISOString();
  handle.record.state = 'deferred';
  handle.record.cleanupFailures = (handle.record.cleanupFailures ?? 0) + 1;
  handle.record.lastCleanupAttemptAt = now;
  handle.record.lastCleanupError = detail;
  handle.record.lastReportedAt = now;
  await writeRecord(handle);
}

/** Normal fixture teardown: delete immediately, then forget ownership. */
export async function cleanupOwnedUser(
  api: OwnedUserAdminApi,
  handle: OwnershipHandle,
  userId: number,
  options: RegistryOptions = {},
): Promise<boolean> {
  const warn = options.warn ?? console.warn;
  if (handle.record.userId !== userId) {
    const detail = `cleanup id ${userId} does not match bound ownership id ${String(handle.record.userId)}`;
    await retainDeferredCleanup(handle, detail, options).catch(() => undefined);
    warn(`[e2e-user-cleanup] Deferred ${handle.record.username}; ${detail}`);
    return false;
  }
  const result = await deleteUserWithRetry(api, userId, options.retryDelayMs ?? 150);
  if (!result.deleted) {
    try {
      await retainDeferredCleanup(handle, result.detail, options);
    } catch (error) {
      // The pre-existing intent remains on disk even if enriching it fails.
      // Cleanup must not replace a product assertion with bookkeeping noise.
      result.detail += `; could not persist deferred detail: ${String(error)}`;
    }
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
    && typeof record.createdAt === 'string'
    && typeof record.username === 'string'
    && USERNAME_PATTERN.test(record.username)
    && record.email === `${record.username}@example.test`
    && (record.runId === undefined || (typeof record.runId === 'string' && record.runId.length > 0))
    && (record.workerId === undefined || (typeof record.workerId === 'string' && record.workerId.length > 0))
    && ((record.runId === undefined) === (record.workerId === undefined))
    && (record.state === undefined || ['intent', 'created', 'deferred'].includes(record.state))
    && (record.cleanupFailures === undefined
      || (typeof record.cleanupFailures === 'number'
        && Number.isSafeInteger(record.cleanupFailures)
        && record.cleanupFailures >= 0))
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
      if (record.runId !== undefined && record.workerId !== undefined
          && !entry.name.startsWith(`${ownershipKey(record.runId, record.workerId)}-`)) {
        options.warn?.(`[e2e-user-reaper] Ignoring ownership record with a mismatched run/worker key ${filePath}`);
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
  api: OwnedUserAdminApi,
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
    users = await api.listUsers();
  } catch (error) {
    summary.deferred = stale.length;
    const stateErrors: string[] = [];
    for (const handle of stale) {
      await retainDeferredCleanup(handle, `user listing failed: ${String(error)}`, options)
        .catch((stateError) => stateErrors.push(String(stateError)));
    }
    const stateDetail = stateErrors.length > 0
      ? `; ${stateErrors.length} deferred record update(s) also failed: ${stateErrors.join('; ')}`
      : '';
    warn(
      `[e2e-user-reaper] Could not list owned users; ${stale.length} cleanup(s) deferred: ${String(error)}${stateDetail}`,
    );
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
        const createdAt = Date.parse(record.createdAt);
        const now = (options.now?.() ?? new Date()).getTime();
        const intentGraceMs = options.intentGraceMs ?? 60_000;
        if (Number.isFinite(createdAt) && now - createdAt >= intentGraceMs) {
          try {
            await forgetRecord(handle);
            summary.absent += 1;
          } catch (error) {
            summary.deferred += 1;
            warn(`[e2e-user-reaper] Could not reconcile expired intent ${record.username}: ${String(error)}`);
          }
        } else {
          summary.deferred += 1;
        }
      } else {
        const conflictingIdentity = users.some((user) => (
          user.id === record.userId
          || (user.name === record.username && user.email === record.email)
        ));
        if (conflictingIdentity) {
          const detail = 'bound id or exact account identity no longer matches the ownership record';
          let reportDetail = detail;
          await retainDeferredCleanup(handle, detail, options).catch((error) => {
            reportDetail += `; could not persist deferred detail: ${String(error)}`;
          });
          summary.deferred += 1;
          warn(`[e2e-user-reaper] Deferred ${record.username}; ${reportDetail}`);
        } else {
          try {
            await forgetRecord(handle);
            summary.absent += 1;
          } catch (error) {
            summary.deferred += 1;
            warn(`[e2e-user-reaper] Could not reconcile absent ${record.username}: ${String(error)}`);
          }
        }
      }
      continue;
    }

    const result = await deleteUserWithRetry(api, exact.id, options.retryDelayMs ?? 150);
    if (!result.deleted) {
      let reportDetail = result.detail;
      await retainDeferredCleanup(handle, result.detail, options).catch((error) => {
        reportDetail += `; could not persist deferred detail: ${String(error)}`;
      });
      summary.deferred += 1;
      warn(
        `[e2e-user-reaper] Deferred stale ${record.username}; durable ownership record retained (${reportDetail})`,
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
