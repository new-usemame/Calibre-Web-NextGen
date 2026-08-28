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

type MultiUserFixtures = { secondaryUser: SecondaryUserSession };

async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as { csrf_token: string }).csrf_token;
}

/** Compatibility copy of ci/1927's fixture contract. When that branch lands,
 * my-library.spec.ts changes only its import path to ./fixtures. */
export const test = base.extend<MultiUserFixtures>({
  secondaryUser: [async ({ page: adminPage, browser, baseURL }, use, testInfo) => {
    const suffix = randomUUID().replaceAll('-', '').slice(0, 12);
    const project = testInfo.project.name.replace(/[^a-z0-9]/gi, '-').slice(0, 12);
    const username = `e2e-${project}-${testInfo.workerIndex}-${suffix}`;
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
      const csrf = await context.request.get('/api/v1/auth/csrf');
      const login = await context.request.post('/api/v1/auth/login', {
        headers: { 'X-CSRFToken': ((await csrf.json()) as { csrf_token: string }).csrf_token },
        data: { username, password, remember: false },
      });
      expect(login.ok(), await login.text()).toBeTruthy();
      await secondaryPage.goto('/app');
      await expect(secondaryPage.getByRole('button', { name: `Account: ${username}` })).toBeVisible();
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
