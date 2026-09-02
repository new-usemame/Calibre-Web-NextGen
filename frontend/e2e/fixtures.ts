import {
  test as base,
  expect,
  type BrowserContext,
  type Page,
} from '@playwright/test';
import {
  cleanupOwnedUser,
  createOwnedUser,
  createOwnedUserIdentity,
} from './user-reaper';
import {
  adminCredentialsFromEnvironment,
  DirectAdminApi,
} from './direct-admin-api';

export interface SecondaryUserSession {
  id: number;
  username: string;
  password: string;
  context: BrowserContext;
  page: Page;
}

type MultiUserFixtures = {
  /**
   * A unique, non-admin viewer in a separate browser context.
   *
   * Creation and deletion use a page-independent direct admin API session;
   * user login uses the production auth endpoint in its own browser context.
   * The fixture is test-scoped, so existing specs pay nothing and parallel
   * workers never share this account.
   */
  secondaryUser: SecondaryUserSession;
};

export const test = base.extend<MultiUserFixtures>({
  secondaryUser: [async ({ browser, baseURL }, use, testInfo) => {
    if (!baseURL) throw new Error('secondaryUser requires Playwright use.baseURL');
    const { username, email } = createOwnedUserIdentity(
      testInfo.project.name,
      testInfo.workerIndex,
    );
    // Keep every policy class deterministic. A UUID slice can (rarely) contain
    // only digits, which made the old generated password probabilistically
    // fail instances requiring a lowercase character.
    const password = `Aa7!zY9@${username.slice(-20)}`;
    const configuredRunId = testInfo.config.metadata.cwngE2ERunId;
    if (typeof configuredRunId !== 'string' || !configuredRunId) {
      throw new Error('secondaryUser requires Playwright metadata.cwngE2ERunId');
    }
    const adminApi = await DirectAdminApi.open(baseURL, adminCredentialsFromEnvironment());
    let owned: Awaited<ReturnType<typeof createOwnedUser>> | undefined;
    let context: BrowserContext | undefined;
    try {
      owned = await createOwnedUser(
        adminApi,
        baseURL,
        {
          name: username,
          email,
          password,
          roles: {
            admin: false,
            viewer: true,
            download: true,
            upload: false,
            edit: false,
            edit_shelfs: false,
            delete_books: false,
          },
        },
        {
          runId: configuredRunId,
          workerId: `${testInfo.project.name}:${testInfo.workerIndex}:${testInfo.parallelIndex}`,
        },
      );
      const { id } = owned.user;

      // Project context options include the global admin storage state. This
      // fixture logs in its own unique account, so start with an explicitly
      // empty jar/store: inherited admin localStorage must not be mistaken for
      // that account's one-time preference adoption input.
      context = await browser.newContext({
        baseURL,
        storageState: { cookies: [], origins: [] },
      });
      const secondaryPage = await context.newPage();
      // BrowserContext.request shares this context's cookie jar. Calling the
      // real login endpoint here creates an independent browser session without
      // repeating the global setup's slower UI-login coverage in every test.
      const secondaryCsrf = await context.request.get('/api/v1/auth/csrf');
      expect(secondaryCsrf.ok()).toBeTruthy();
      const loginResponse = await context.request.post('/api/v1/auth/login', {
        headers: {
          'X-CSRFToken': ((await secondaryCsrf.json()) as { csrf_token: string }).csrf_token,
        },
        data: { username, password, remember: false },
      });
      expect(loginResponse.ok(), await loginResponse.text()).toBeTruthy();
      await secondaryPage.goto('/app');
      await expect(secondaryPage.getByRole('button', { name: `Account: ${username}` })).toBeVisible();
      const meResponse = await secondaryPage.request.get('/api/v1/auth/me');
      expect(meResponse.ok(), 'secondary browser session should be authenticated').toBeTruthy();
      const me = (await meResponse.json()) as {
        name: string;
        role: { admin: boolean };
      };
      expect(me.name).toBe(username);
      expect(me.role.admin).toBe(false);

      await use({ id, username, password, context, page: secondaryPage });
    } finally {
      await context?.close().catch(() => undefined);
      // This API session owns a separate cookie jar and survives both browser
      // page and secondary context closure. Bounded failure is deferred on disk
      // rather than replacing an assertion thrown by the product flow.
      if (owned) {
        await cleanupOwnedUser(adminApi, owned.ownership, owned.user.id);
      }
      await adminApi.dispose().catch(() => undefined);
    }
  }, { timeout: 120_000 }],
});

export { expect } from '@playwright/test';
