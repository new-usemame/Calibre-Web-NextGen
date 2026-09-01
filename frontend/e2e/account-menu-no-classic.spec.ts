import { test, expect, type Page } from '@playwright/test';

/*
 * The account (avatar) menu no longer offers a switch to the Classic UI, and the
 * items around the removed one still work.
 *
 * Why an e2e and not only the structural unit pin: the unit test reads the
 * source, so it proves the JSX no longer names the control. It cannot prove the
 * SHIPPED bundle renders a working menu — a bad edit that left a dangling
 * handler, or an item whose href stopped resolving, would keep the unit pin
 * green. This drives the real container: open the menu, assert the Classic entry
 * is absent by every name it ever had, then actually navigate each survivor.
 */

async function openAccountMenu(page: Page) {
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible({ timeout: 20_000 });
  const trigger = page.getByRole('button', { name: /account:/i });
  await expect(trigger).toBeVisible();
  await trigger.click();
  // Scope to the wrapper that owns the trigger so nothing here can accidentally
  // match the sidebar rail's own links.
  return trigger.locator('xpath=ancestor::div[1]');
}

test.describe('account menu — no Classic switch', () => {
  test('the menu has no Classic entry and its remaining items still navigate', async ({ page }) => {
    const menu = await openAccountMenu(page);

    // Absent by label, and absent by the marker URL the removed handler used —
    // a renamed-but-still-present control would pass the first check alone.
    await expect(menu.getByText(/classic/i)).toHaveCount(0);
    await expect(menu.locator('a[href*="cwng_feedback"]')).toHaveCount(0);

    // Sign out is present (the seeded e2e user is signed in), and My account /
    // Admin are real links, not placeholders.
    await expect(menu.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible();
    await expect(menu.getByRole('link', { name: 'My account', exact: true }))
      .toHaveAttribute('href', /\/account$/);
    await expect(menu.getByRole('link', { name: 'Admin', exact: true }))
      .toHaveAttribute('href', /\/admin$/);

    // Navigate one survivor for real; a link that renders but does not route is
    // exactly the failure a presence assertion cannot see.
    await menu.getByRole('link', { name: 'My account', exact: true }).click();
    await expect(page).toHaveURL(/\/account(\/|$|\?)/);
  });

  test('signing out from the menu ends the session and returns to the new UI', async ({
    browser, baseURL,
  }, testInfo) => {
    // A DEDICATED context that logs itself in. The shared storageState session is
    // reused by every other spec running in parallel; clicking Sign out on it
    // logs those specs out mid-run, which is a real failure this suite already
    // caused once and which looks exactly like an unrelated login regression.
    //
    // A separate context is NOT enough, and that is the whole reason for the
    // userAgent below. The server keys its session rows on Flask-Login's `_id`,
    // which is sha512(remote address | User-Agent) -- a browser fingerprint, not
    // a per-context id. An explicit browser.newContext() does not inherit the
    // project's `use` block, so it runs with the engine default UA, which is
    // byte-identical to the one global.setup.ts seeded storageState with. Same
    // address + same UA => same session key, and cps/logout.py deletes by
    // (user_id, session_key), so Sign out here would delete the shared seeded
    // row and 401 every spec still running. Giving this context its own UA gives
    // it its own key. The assertion below is the guard: drop the userAgent and
    // THIS test fails by name, instead of ~150 unrelated specs failing later.
    // tests/unit/test_e2e_session_fingerprint_isolation.py pins the mechanism.
    const defaultContext = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    const defaultPage = await defaultContext.newPage();
    const defaultUserAgent = await defaultPage.evaluate(() => navigator.userAgent);
    await defaultContext.close();
    const isolatedUserAgent = `${defaultUserAgent} CWNG-E2E-signout/${testInfo.project.name}`;

    const context = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
      userAgent: isolatedUserAgent,
    });
    try {
      const page = await context.newPage();
      const actualUserAgent = await page.evaluate(() => navigator.userAgent);
      expect(
        actualUserAgent,
        'sign-out context must use its own server-side session fingerprint',
      ).toBe(isolatedUserAgent);

      await page.goto('/app/login');
      await page.locator('input[autocomplete="username"]').fill(process.env.E2E_USER || 'admin');
      await page.locator('input[autocomplete="current-password"]').fill(process.env.E2E_PASS || 'admin123');
      await page.getByRole('button', { name: /sign in/i }).click();
      await expect(page).toHaveURL(/\/app(\/|$|\?)/, { timeout: 20_000 });

      const trigger = page.getByRole('button', { name: /account:/i });
      await expect(trigger).toBeVisible({ timeout: 20_000 });
      await trigger.click();
      await trigger.locator('xpath=ancestor::div[1]')
        .getByRole('button', { name: 'Sign out', exact: true }).click();

      // Anonymous browsing is an instance setting, not an invariant of either
      // E2E rig. Ask /auth/me after logout because its Guest-vs-401 contract is
      // the server truth; inferring the setting from whichever control happens
      // to render would let a broken UI choose the assertion it can pass.
      await expect(page).toHaveURL(/\/app\/?(\?|$)/, { timeout: 20_000 });
      const postSignOutMeResponse = await page.request.get('/api/v1/auth/me');
      const postSignOutMe = await postSignOutMeResponse.json() as {
        role?: { anonymous?: boolean };
        features?: { anon_browse?: boolean };
        error?: { code?: string };
      };
      const anonymousBrowsingEnabled = postSignOutMeResponse.status() === 200
        && postSignOutMe.role?.anonymous === true
        && postSignOutMe.features?.anon_browse === true;
      const anonymousBrowsingDisabled = postSignOutMeResponse.status() === 401
        && postSignOutMe.error?.code === 'unauthenticated';
      expect(
        Number(anonymousBrowsingEnabled) + Number(anonymousBrowsingDisabled),
        `post-sign-out /auth/me must report exactly one server configuration; observed status ${postSignOutMeResponse.status()}, anonymous=${String(postSignOutMe.role?.anonymous)}, anon_browse=${String(postSignOutMe.features?.anon_browse)}, error=${String(postSignOutMe.error?.code)}`,
      ).toBe(1);

      // These are configuration-independent consequences of logout. Keep them
      // ahead of the UI branch so neither server mode can bypass session and
      // new-UI persistence checks that are the subject of this test.
      await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toHaveCount(0);
      const cookies = await context.cookies();
      expect(cookies.find((c) => c.name === 'remember_token')).toBeUndefined();
      expect(cookies.find((c) => c.name === 'cwng_prefer_classic')).toBeUndefined();
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/app\/?(\?|$)/);

      const guestTrigger = page.getByRole('button', { name: /account: guest/i });
      const loginButton = page.getByRole('button', { name: 'Sign in', exact: true });
      const expectedPostSignOutState = anonymousBrowsingEnabled
        ? 'Guest account menu'
        : 'login form';

      // Wait for either legitimate tree before describing what rendered. This
      // keeps a slow app bootstrap from being mislabeled as a third state, while
      // the message makes a genuine neither-state failure actionable.
      await expect(
        guestTrigger.or(loginButton),
        `post-sign-out state mismatch: server expected ${expectedPostSignOutState}; observed neither the Guest account menu nor the login form`,
      ).toBeVisible({ timeout: 20_000 });
      const guestTriggerVisible = await guestTrigger.isVisible();
      const loginButtonVisible = await loginButton.isVisible();
      const observedPostSignOutState = guestTriggerVisible && loginButtonVisible
        ? 'both the Guest account menu and the login form'
        : guestTriggerVisible
          ? 'Guest account menu'
          : loginButtonVisible
            ? 'login form'
            : 'neither the Guest account menu nor the login form';

      if (anonymousBrowsingEnabled) {
        expect(
          observedPostSignOutState,
          `post-sign-out state mismatch: server expected Guest account menu; observed ${observedPostSignOutState}`,
        ).toBe('Guest account menu');
        await guestTrigger.click();
        const guestMenu = guestTrigger.locator('xpath=ancestor::div[1]');
        await expect(guestMenu.getByRole('link', { name: 'Sign in', exact: true })).toBeVisible();
        await expect(guestMenu.getByRole('button', { name: 'Sign out', exact: true })).toHaveCount(0);
      } else {
        expect(
          observedPostSignOutState,
          `post-sign-out state mismatch: server expected login form; observed ${observedPostSignOutState}`,
        ).toBe('login form');
        await expect(page.locator('input[autocomplete="username"]')).toBeVisible();
        await expect(loginButton).toBeVisible();
        await expect(guestTrigger).toHaveCount(0);
      }
    } finally {
      await context.close();
    }
  });
});
