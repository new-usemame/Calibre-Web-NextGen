import {
  request as playwrightRequest,
  type APIRequestContext,
  type APIResponse,
} from '@playwright/test';

import type {
  AdminUser,
  OwnedUserAdminApi,
  OwnedUserCreate,
} from './user-reaper';

const REQUEST_TIMEOUT_MS = 5_000;

export interface AdminCredentials {
  username: string;
  password: string;
}

async function responseDetail(response: APIResponse): Promise<string> {
  const body = await response.text().catch(() => '');
  return `HTTP ${response.status()}${body ? ` ${body}` : ''}`;
}

async function csrfToken(context: APIRequestContext): Promise<string> {
  const response = await context.get('/api/v1/auth/csrf', {
    timeout: REQUEST_TIMEOUT_MS,
  });
  if (!response.ok()) {
    throw new Error(`CSRF request failed: ${await responseDetail(response)}`);
  }
  const payload = (await response.json()) as { csrf_token?: unknown };
  if (typeof payload.csrf_token !== 'string' || !payload.csrf_token) {
    throw new Error('CSRF endpoint returned no csrf_token');
  }
  return payload.csrf_token;
}

/**
 * An isolated request context with its own login cookie jar. It is never
 * derived from a browser page, so page closure cannot invalidate cleanup.
 */
export class DirectAdminApi implements OwnedUserAdminApi {
  private constructor(private readonly context: APIRequestContext) {}

  static async open(baseURL: string, credentials: AdminCredentials): Promise<DirectAdminApi> {
    const context = await playwrightRequest.newContext({ baseURL });
    try {
      const response = await context.post('/api/v1/auth/login', {
        headers: { 'X-CSRFToken': await csrfToken(context) },
        data: {
          username: credentials.username,
          password: credentials.password,
          remember: false,
        },
        timeout: REQUEST_TIMEOUT_MS,
      });
      if (!response.ok()) {
        throw new Error(`Direct admin login failed: ${await responseDetail(response)}`);
      }
      return new DirectAdminApi(context);
    } catch (error) {
      await context.dispose().catch(() => undefined);
      throw error;
    }
  }

  async createUser(input: OwnedUserCreate): Promise<AdminUser> {
    const response = await this.context.post('/api/v1/admin/users', {
      headers: { 'X-CSRFToken': await csrfToken(this.context) },
      data: input,
      timeout: REQUEST_TIMEOUT_MS,
    });
    if (response.status() !== 201) {
      throw new Error(`Secondary-user create failed: ${await responseDetail(response)}`);
    }
    const payload = (await response.json()) as Partial<AdminUser>;
    if (!Number.isSafeInteger(payload.id)
        || typeof payload.name !== 'string'
        || typeof payload.email !== 'string') {
      throw new Error('Secondary-user create returned an invalid identity payload');
    }
    return payload as AdminUser;
  }

  async listUsers(): Promise<AdminUser[]> {
    const response = await this.context.get('/api/v1/admin/users', {
      timeout: REQUEST_TIMEOUT_MS,
    });
    if (!response.ok()) {
      throw new Error(`Admin user listing failed: ${await responseDetail(response)}`);
    }
    const payload = (await response.json()) as { items?: unknown };
    if (!Array.isArray(payload.items)) {
      throw new Error('Admin user listing returned no items array');
    }
    const users: AdminUser[] = [];
    for (const item of payload.items) {
      if (!item || typeof item !== 'object') {
        throw new Error('Admin user listing returned an invalid user identity');
      }
      const user = item as Partial<AdminUser>;
      if (!Number.isSafeInteger(user.id)
          || typeof user.name !== 'string'
          || typeof user.email !== 'string') {
        throw new Error('Admin user listing returned an invalid user identity');
      }
      users.push(user as AdminUser);
    }
    return users;
  }

  async deleteUser(userId: number): Promise<{ deleted: boolean; detail: string }> {
    const response = await this.context.post(`/api/v1/admin/users/${userId}/delete`, {
      headers: { 'X-CSRFToken': await csrfToken(this.context) },
      timeout: REQUEST_TIMEOUT_MS,
    });
    if (response.status() === 204 || response.status() === 404) {
      return { deleted: true, detail: `HTTP ${response.status()}` };
    }
    return { deleted: false, detail: await responseDetail(response) };
  }

  async dispose(): Promise<void> {
    await this.context.dispose();
  }
}

export function adminCredentialsFromEnvironment(): AdminCredentials {
  return {
    username: process.env.E2E_USER || 'admin',
    password: process.env.E2E_PASS || 'admin123',
  };
}
