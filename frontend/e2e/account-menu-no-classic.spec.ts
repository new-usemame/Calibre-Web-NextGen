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

      // This rig permits anonymous browsing, so /logout returns to the SPA
      // library as Guest instead of forcing the login route. Prove the identity
      // transition itself: the account control and its actions must switch from
      // the authenticated user/Sign out to Guest/Sign in, and remember-me must
      // be gone.
      await expect(page).toHaveURL(/\/app\/?(\?|$)/, { timeout: 20_000 });
      const guestTrigger = page.getByRole('button', { name: /account: guest/i });
      await expect(guestTrigger).toBeVisible();
      await guestTrigger.click();
      const guestMenu = guestTrigger.locator('xpath=ancestor::div[1]');
      await expect(guestMenu.getByRole('link', { name: 'Sign in', exact: true })).toBeVisible();
      await expect(guestMenu.getByRole('button', { name: 'Sign out', exact: true })).toHaveCount(0);

      // Signing out never leaves a Classic selection behind, and the next visit
      // to the root still opens the new UI.
      const cookies = await context.cookies();
      expect(cookies.find((c) => c.name === 'remember_token')).toBeUndefined();
      expect(cookies.find((c) => c.name === 'cwng_prefer_classic')).toBeUndefined();
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/app\/?(\?|$)/);
    } finally {
      await context.close();
    }
  });
});
