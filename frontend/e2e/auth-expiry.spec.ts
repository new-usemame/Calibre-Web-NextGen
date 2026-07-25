import { test, expect, Page } from '@playwright/test';

async function holdLogoutNavigation(page: Page) {
  let navigations = 0;
  await page.route('**/logout', async (route) => {
    if (route.request().isNavigationRequest()) navigations += 1;
    await route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Logout captured</title>' });
  });
  return () => navigations;
}

/** End the session underneath a booted app.
 *
 * These tests used to simulate expiry by failing a single protected endpoint
 * while the session was in fact still valid. That was enough back when the SPA
 * inferred "signed out" from the failure itself — but inferring it was the #1067
 * bug: the remedy (navigating to /logout) deletes the session server-side, so a
 * NAS dropping one request signed people out for real. The SPA now confirms with
 * /api/v1/auth/me before spending that remedy, so a test that wants the expiry
 * path has to actually expire the session rather than only break one call.
 *
 * The first /me is App's bootstrap and must succeed, or the logged-out tree
 * renders and the protected call under test never fires. Every later /me is the
 * confirmation probe. */
async function endSessionAfterBootstrap(page: Page) {
  let calls = 0;
  await page.route('**/api/v1/auth/me', async (route) => {
    calls += 1;
    if (calls === 1) return route.continue();
    return route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
    });
  });
}

async function expectProtectedFailureNavigates(
  page: Page,
  failure: (route: import('@playwright/test').Route) => Promise<void>,
) {
  const navigationCount = await holdLogoutNavigation(page);
  await endSessionAfterBootstrap(page);
  await page.route('**/api/v1/books?**', failure);
  await page.goto('/app');
  await expect(page).toHaveURL(/\/logout$/);
  expect(navigationCount()).toBe(1);
}

test.describe('expired authenticated session (#824)', () => {
  test('rejected protected fetch navigates to canonical logout', async ({ page }) => {
    await expectProtectedFailureNavigates(page, (route) => route.abort());
  });

  test('redirected protected fetch navigates to canonical logout', async ({ page }) => {
    await expectProtectedFailureNavigates(page, (route) => route.fulfill({
      status: 302,
    }));
  });

  test('401 protected response navigates to canonical logout', async ({ page }) => {
    await expectProtectedFailureNavigates(page, (route) => route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
    }));
  });

  test('400 validation response never navigates', async ({ page }) => {
    let logoutNavigations = 0;
    page.on('request', (request) => {
      if (request.isNavigationRequest() && new URL(request.url()).pathname.endsWith('/logout')) {
        logoutNavigations += 1;
      }
    });
    await page.route('**/api/v1/books?**', (route) => route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'validation', message: 'Bad request' } }),
    }));
    await page.goto('/app');
    await page.waitForTimeout(500);
    expect(logoutNavigations).toBe(0);
    await expect(page).toHaveURL(/\/app(?:\/|$|\?)/);
  });

  test('two concurrent protected failures cause one navigation', async ({ page }) => {
    const navigationCount = await holdLogoutNavigation(page);
    await endSessionAfterBootstrap(page);
    let failedRequests = 0;
    await page.route('**/api/v1/admin/**', (route) => {
      failedRequests += 1;
      return route.abort();
    });
    // Playwright routes are checked last-registered first, so this exact route
    // lets the parent render before the three child config queries fail together.
    await page.route('**/api/v1/admin/users', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    }));
    await page.goto('/app/admin');
    await expect(page).toHaveURL(/\/logout$/);
    await page.waitForTimeout(250);
    expect(failedRequests).toBeGreaterThanOrEqual(2);
    expect(navigationCount()).toBe(1);
  });
});

/** #1074 narrowed the "session gone" verdict so a visitor who was never signed
 *  in is not logged out for the 401s that anonymous browsing answers by design.
 *  The narrowing keys off having observed an authenticated /auth/me — which
 *  App's bootstrap read supplies on a page load, but NOT on a sign-in that
 *  happens inside an already-booted app: useLogin seeds the me-cache directly,
 *  so the app flips to the authenticated tree and starts issuing protected
 *  calls a full round trip before the invalidation refetch answers.
 *
 *  A session that dies inside that window is a real loss, and it must still be
 *  classified as one. Everything here is route-mocked so the case is exact and
 *  needs no server-side anonymous-browsing config: /auth/me answers 200-with-
 *  Guest (the #1023 shape anonymous browsing produces), which is the only shape
 *  where the narrowing applies at all — a 401 probe is still an unconditional
 *  "gone" (api.ts probeSession). */
test('session lost right after an in-app sign-in still navigates to logout (#1074)', async ({ page }) => {
  await page.context().clearCookies();
  const navigationCount = await holdLogoutNavigation(page);

  const guestBody = JSON.stringify({
    id: 2, name: 'Guest', locale: 'en', theme: 'dark', role: { anonymous: true, viewer: true },
  });

  let meCalls = 0;
  await page.route('**/api/v1/auth/me', async (route) => {
    meCalls += 1;
    // Bootstrap: nobody is signed in, so the login tree renders.
    if (meCalls === 1) {
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
      });
    }
    // Every later read — the post-login invalidation refetch and the probe —
    // reports Guest: the session died between the sign-in and this answer. The
    // refetch is held open so the protected call below lands inside the window
    // rather than after it, which is the whole point of the test.
    if (meCalls === 2) await new Promise((resolve) => setTimeout(resolve, 3000));
    return route.fulfill({ status: 200, contentType: 'application/json', body: guestBody });
  });

  await page.route('**/api/v1/auth/login', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      id: 1, name: 'e2e', locale: 'en', theme: 'dark', role: { admin: true, viewer: true, download: true },
    }),
  }));

  // The first protected call the authenticated tree makes, answered as the
  // dead session would answer it.
  await page.route('**/api/v1/books?**', (route) => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
  }));

  await page.goto('/app');
  await page.locator('input[autocomplete="username"]').fill('e2e');
  await page.locator('input[autocomplete="current-password"]').fill('e2e');
  await page.getByRole('button', { name: /sign in/i }).click();

  await expect(page).toHaveURL(/\/logout$/);
  expect(navigationCount()).toBe(1);
});

test.describe('public authentication guards', () => {
  test('login 401 remains invalid credentials and does not navigate', async ({ page }) => {
    await page.context().clearCookies();
    let logoutNavigations = 0;
    page.on('request', (request) => {
      if (request.isNavigationRequest() && new URL(request.url()).pathname.endsWith('/logout')) {
        logoutNavigations += 1;
      }
    });
    await page.route('**/api/v1/auth/login', (route) => route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'invalid_credentials', message: 'Invalid username or password' } }),
    }));
    await page.goto('/app');
    await page.locator('input[autocomplete="username"]').fill('wrong');
    await page.locator('input[autocomplete="current-password"]').fill('wrong');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText('Invalid username or password.', { exact: true })).toBeVisible();
    expect(logoutNavigations).toBe(0);
    await expect(page).toHaveURL(/\/app(?:\/|$|\?)/);
  });

  test('rejected public login fetch does not navigate', async ({ page }) => {
    await page.context().clearCookies();
    let logoutNavigations = 0;
    page.on('request', (request) => {
      if (request.isNavigationRequest() && new URL(request.url()).pathname.endsWith('/logout')) {
        logoutNavigations += 1;
      }
    });
    await page.route('**/api/v1/auth/login', (route) => route.abort());
    await page.goto('/app');
    await page.locator('input[autocomplete="username"]').fill('offline');
    await page.locator('input[autocomplete="current-password"]').fill('offline');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByText('Sign in failed. Please try again.', { exact: true })).toBeVisible();
    expect(logoutNavigations).toBe(0);
    await expect(page).toHaveURL(/\/app(?:\/|$|\?)/);
  });
});

test('Sign out performs a top-level navigation to /logout (#674)', async ({ page }) => {
  let apiLogoutPosts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/api/v1/auth/logout')) {
      apiLogoutPosts += 1;
    }
  });
  await holdLogoutNavigation(page);
  await page.goto('/app');
  const account = page.getByRole('button', { name: /account:/i });
  await account.click();
  await page.getByText('Sign out', { exact: true }).click();
  await expect(page).toHaveURL(/\/logout$/);
  expect(apiLogoutPosts).toBe(0);
});
