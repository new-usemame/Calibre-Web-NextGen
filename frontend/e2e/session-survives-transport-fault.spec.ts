import { test, expect, Page } from '@playwright/test';

/*
 * #1067 — "Forced back to sign in page when clicking on shelves".
 *
 * Reported on v4.1.19 against a QNAP TS-453A: opening one shelf worked, opening
 * the next spun and then landed on the login page, and re-ticking "remember me"
 * never helped. Some book covers failed to render in the same sessions.
 *
 * Root cause was in the SPA, not the session layer. classifiedFetch treated a
 * REJECTED fetch — the transport-level failure a busy NAS produces when it drops
 * a request — as proof that the session had expired, and its remedy was a hard
 * navigation to /logout. That route is not cosmetic: cps/logout.py's
 * cleanup_local_logout deletes the User_Sessions row and clears the remember-me
 * cookie. So a single dropped request destroyed a perfectly valid session, which
 * is why "remember me" could not bring the reporter back and why it recurred on
 * every few shelf clicks.
 *
 * The fix confirms with /api/v1/auth/me before spending that remedy. These tests
 * pin both directions: a transport fault must keep the user signed in, and a
 * genuinely ended session must still return them to login (#824).
 *
 * The session-liveness assertion is the load-bearing one — /logout is left
 * un-intercepted on purpose in the first test, so a regression fails by actually
 * destroying the session rather than merely navigating.
 */

/** Count top-level navigations to /logout without letting them destroy the
 *  session, for the cases where the navigation itself is the assertion. */
async function holdLogoutNavigation(page: Page) {
  let navigations = 0;
  await page.route('**/logout', async (route) => {
    if (route.request().isNavigationRequest()) navigations += 1;
    await route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Logout captured</title>' });
  });
  return () => navigations;
}

function countLogoutNavigations(page: Page) {
  let navigations = 0;
  page.on('request', (request) => {
    if (request.isNavigationRequest() && new URL(request.url()).pathname.endsWith('/logout')) {
      navigations += 1;
    }
  });
  return () => navigations;
}

test.describe('#1067 a dropped request must not end the session', () => {
  test('shelves failing at the transport layer keeps the user signed in', async ({ page }) => {
    const logoutNavigations = countLogoutNavigations(page);

    // The NAS drops this one request. Nothing about the session has changed.
    await page.route('**/api/v1/shelves**', (route) => route.abort());

    await page.goto('/app/shelves');
    await page.waitForTimeout(1500);

    // The user stays where they were rather than being bounced to the login page.
    expect(logoutNavigations(), 'a dropped request must not trigger a logout').toBe(0);
    await expect(page).toHaveURL(/\/app\/shelves/);

    // And — the severe half — the server-side session is still real, so the next
    // click works instead of landing on a login form.
    const me = await page.request.get('/api/v1/auth/me');
    expect(me.status(), 'the session must survive a dropped request').toBe(200);
    const body = (await me.json()) as { role?: { anonymous?: boolean } };
    expect(body.role?.anonymous ?? false, 'user must still be signed in').toBe(false);
  });

  test('concurrent transport faults share one confirmation probe', async ({ page }) => {
    // The confirmation must not become its own stampede: when a struggling server
    // drops the several requests a page had in flight, it should field one probe,
    // not one per failure.
    //
    // "Concurrent" has to be constructed rather than hoped for. Aborting each
    // request as it arrives lets the admin page's queries fail in a staggered
    // sequence, and sequentially-failing requests legitimately get their own
    // probe (the session's state can change between them) — so the requests are
    // held until several are in flight and then failed in the same tick, which is
    // the situation single-flight actually exists for.
    const HOLD = 3;
    let meCalls = 0;
    await page.route('**/api/v1/auth/me', (route) => {
      meCalls += 1;
      return route.continue();
    });

    let held: import('@playwright/test').Route[] = [];
    let failedTogether = 0;
    const releaseAll = async () => {
      const batch = held;
      held = [];
      failedTogether += batch.length;
      await Promise.all(batch.map((r) => r.abort().catch(() => {})));
    };
    await page.route('**/api/v1/books**', async (route) => {
      held.push(route);
      if (held.length >= HOLD) await releaseAll();
    });

    await page.goto('/app');
    // Release whatever accumulated if the page issued fewer than HOLD in parallel,
    // so the test fails on its assertion rather than hanging.
    await page.waitForTimeout(3000);
    await releaseAll();
    await page.waitForTimeout(1500);

    expect(failedTogether, 'needs simultaneous failures to be meaningful').toBeGreaterThanOrEqual(2);
    // One bootstrap /me plus one shared probe. Without single-flight this would be
    // 1 + failedTogether.
    expect(meCalls, 'simultaneous failures must share one probe').toBeLessThanOrEqual(2);
  });

  test('a route-level 401 with a live session does not sign the user out', async ({ page }) => {
    const logoutNavigations = countLogoutNavigations(page);

    // A permission-style 401 from one endpoint. /api/v1/auth/me is left alone,
    // so the probe confirms the session is fine and the app surfaces an error.
    await page.route('**/api/v1/shelves**', (route) => route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unauthorized', message: 'Authentication required' } }),
    }));

    await page.goto('/app/shelves');
    await page.waitForTimeout(1500);

    expect(logoutNavigations()).toBe(0);
    const me = await page.request.get('/api/v1/auth/me');
    expect(me.status()).toBe(200);
  });
});

/** Let the app boot as a signed-in user, then have the session end underneath
 *  it. The first /me is App's own bootstrap — 401ing that instead would render
 *  the logged-out tree, and the protected call under test would never fire. Every
 *  later /me is the confirmation probe, and answers the way an ended session does. */
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

test.describe('#824 a genuinely ended session still returns to login', () => {
  test('transport fault plus an unauthenticated probe navigates to logout', async ({ page }) => {
    const navigationCount = await holdLogoutNavigation(page);
    await endSessionAfterBootstrap(page);
    await page.route('**/api/v1/books?**', (route) => route.abort());

    await page.goto('/app');
    await expect(page).toHaveURL(/\/logout$/);
    expect(navigationCount()).toBe(1);
  });

  test('401 response plus an unauthenticated probe navigates to logout', async ({ page }) => {
    const navigationCount = await holdLogoutNavigation(page);
    await endSessionAfterBootstrap(page);
    await page.route('**/api/v1/books?**', (route) => route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
    }));

    await page.goto('/app');
    await expect(page).toHaveURL(/\/logout$/);
    expect(navigationCount()).toBe(1);
  });
});
