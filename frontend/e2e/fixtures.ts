import {
  test as base,
  expect,
  type BrowserContext,
  type Page,
} from '@playwright/test';
import { randomUUID } from 'node:crypto';

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
   * Creation and deletion use the primary admin's authenticated API session;
   * login uses the production auth endpoint in its own browser context. The
   * fixture is test-scoped, so existing specs pay nothing and parallel workers
   * never share this account.
   */
  secondaryUser: SecondaryUserSession;
};

async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok(), 'CSRF endpoint must answer while arranging the secondary user').toBeTruthy();
  return ((await response.json()) as { csrf_token: string }).csrf_token;
}

export const test = base.extend<MultiUserFixtures>({
  secondaryUser: [async ({ page: adminPage, browser, baseURL }, use, testInfo) => {
    const suffix = randomUUID().replaceAll('-', '').slice(0, 12);
    const project = testInfo.project.name.replace(/[^a-z0-9]/gi, '-').slice(0, 12);
    const username = `e2e-${project}-${testInfo.workerIndex}-${suffix}`;
    // Keep every policy class deterministic. A UUID slice can (rarely) contain
    // only digits, which made the old generated password probabilistically
    // fail instances requiring a lowercase character.
    const password = `Aa7!zY9@${suffix}`;
    const created = await adminPage.request.post('/api/v1/admin/users', {
      headers: { 'X-CSRFToken': await csrfToken(adminPage) },
      data: {
        name: username,
        email: `${username}@example.test`,
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
    });
    expect(created.status(), await created.text()).toBe(201);
    const { id } = (await created.json()) as { id: number };

    let context: BrowserContext | undefined;
    try {
      context = await browser.newContext({ baseURL });
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
      const deleted = await adminPage.request.post(`/api/v1/admin/users/${id}/delete`, {
        headers: { 'X-CSRFToken': await csrfToken(adminPage) },
      });
      expect(deleted.status(), await deleted.text()).toBe(204);
    }
  }, { timeout: 15_000 }],
});

export { expect } from '@playwright/test';
