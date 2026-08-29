import { test, expect } from '@playwright/test';

const STORAGE = 'e2e/.auth/state.json';

async function expectLibrary(page: import('@playwright/test').Page) {
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("This page doesn't exist here.", { exact: true })).toHaveCount(0);
}

test.describe('SPA post-auth destination', () => {
  test('password login at /app/login?next=%2F lands on the library, not the in-app 404', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'one real password login is sufficient for this redirect contract');
    await page.context().clearCookies();

    await page.goto('/app/login?next=%2F');
    await page.locator('input[autocomplete="username"]').fill(process.env.E2E_USER || 'admin');
    await page.locator('input[autocomplete="current-password"]').fill(process.env.E2E_PASS || 'admin123');
    await page.getByRole('button', { name: /sign in/i }).click();

    await expect(page).toHaveURL(/\/app\/?$/);
    await expectLibrary(page);
  });

  test('authenticated stale /login with no next falls back to the library', async ({ page }) => {
    await page.goto('/app/login');
    await expect(page).toHaveURL(/\/app\/?$/);
    await expectLibrary(page);
  });

  test('safe next to a deep SPA route stays client-side', async ({ page }) => {
    await page.goto('/app');
    const bookHref = await page.locator('a[href*="/app/book/"]').first().getAttribute('href');
    expect(bookHref).toBeTruthy();

    await page.goto(`/app/login?next=${encodeURIComponent(bookHref!)}`);
    await expect(page).toHaveURL(new RegExp(`${bookHref!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`));
    await expect(page.getByText("This page doesn't exist here.", { exact: true })).toHaveCount(0);
    await expect(page.locator('main h1')).toBeVisible();
  });

  test('safe next to a classic-only route performs a full-page navigation', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'classic destination behavior is viewport-independent');
    await page.goto(`/app/login?next=${encodeURIComponent('/admin/config')}`);

    await expect(page).toHaveURL(/\/admin\/config(?:\?|$)/);
    await expect(page.getByText("This page doesn't exist here.", { exact: true })).toHaveCount(0);
  });

  for (const maliciousNext of [
    'https://evil.tld',
    '//evil.tld',
    '/\\evil.tld',
    '/..//evil.tld',
    '/.//evil.tld',
    '/a/../..//evil.tld',
    'javascript:alert(1)',
    'data:text/html,phish',
  ]) {
    test(`rejects off-origin next ${maliciousNext}`, async ({ page }) => {
      await page.goto(`/app/login?next=${encodeURIComponent(maliciousNext)}`);

      await expect(page).toHaveURL(/\/app\/?$/);
      expect(new URL(page.url()).origin).toBe(test.info().project.use.baseURL);
      await expectLibrary(page);
    });
  }

  test('magic-link login preserves a deep next destination after the success delay', async ({ page, browser, baseURL }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'one real magic-link flow is sufficient for this redirect contract');
    const verifierContext = await browser.newContext({ baseURL, storageState: STORAGE });
    const verifierPage = await verifierContext.newPage();
    await verifierPage.goto('/app');
    const bookHref = await verifierPage.locator('a[href*="/app/book/"]').first().getAttribute('href');
    expect(bookHref).toBeTruthy();

    await page.context().clearCookies();
    const startResponse = page.waitForResponse('**/api/v1/auth/magic-link/start');

    await page.goto(`/app/magic-link?next=${encodeURIComponent(bookHref!)}`);
    const started = await startResponse;
    expect(started.ok()).toBeTruthy();
    const { verify_url: verifyUrl } = await started.json() as { verify_url: string };
    expect(verifyUrl).toBeTruthy();

    await verifierPage.goto(verifyUrl);

    await expect(page).toHaveURL(new RegExp(`${bookHref!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`), { timeout: 20_000 });
    await page.waitForTimeout(700);
    await expect(page).toHaveURL(new RegExp(`${bookHref!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`));
    await expect(page.locator('main h1')).toBeVisible();
    await verifierContext.close();
  });

  test('magic-link polling retries a 5xx, then stops visibly on a 429', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'poll scheduling is viewport-independent');
    await page.context().clearCookies();
    let polls = 0;

    await page.route('**/api/v1/auth/magic-link/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          token: 'poll-error-token',
          verify_url: 'http://example.test/verify/poll-error-token',
          qrcode: '',
          expires_in_minutes: 10,
        }),
      });
    });
    await page.route('**/api/v1/auth/magic-link/poll', async (route) => {
      polls += 1;
      if (polls === 1) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'unavailable', message: 'Try again' } }),
        });
        return;
      }
      await route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'rate_limit_exceeded',
            message: 'Too many requests',
          },
        }),
      });
    });

    await page.goto('/app/magic-link');
    await expect(page.getByText(
      'Too many magic-link checks were made from this network. Generate a new link or try again later.',
      { exact: true },
    )).toBeVisible({ timeout: 12_000 });
    expect(polls).toBe(2);

    await page.waitForTimeout(4_000);
    expect(polls, 'a fatal 429 must end the poll loop').toBe(2);
  });
});
