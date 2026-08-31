import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';
import { reapOwnedE2EUsers } from './user-reaper';

/*
 * Log in once via the real UI (exercises the CSRF+session flow users hit) and
 * persist the session so authed specs don't re-login. Default creds are the
 * cwn-local seed (admin / admin123); override with E2E_USER / E2E_PASS.
 */
const STORAGE = 'e2e/.auth/state.json';
const USER = process.env.E2E_USER || 'admin';
const PASS = process.env.E2E_PASS || 'admin123';

setup('authenticate', async ({ page, baseURL }) => {
  fs.mkdirSync('e2e/.auth', { recursive: true });

  // Go to the SPA's login ROUTE, not the shell. /app only shows a login form
  // when the instance requires auth to browse; with anonymous browsing enabled
  // (config_anonbrowse=1) it renders the guest library instead, so there is no
  // username field and every spec in the run dies here on a 45s timeout before
  // one of them executes. /app/login renders the form in both configurations.
  //
  // The bare /login route is the legacy Jinja page and is not what the SPA
  // specs should be exercising.
  await page.goto('/app/login');
  await page.locator('input[autocomplete="username"]').fill(USER);
  await page.locator('input[autocomplete="current-password"]').fill(PASS);
  await page.getByRole('button', { name: /sign in/i }).click();

  // Success = we leave the login route and the authed shell renders a book link.
  await expect(page).toHaveURL(/\/app(\/|$|\?)/, { timeout: 20_000 });
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible({ timeout: 20_000 });

  if (!baseURL) throw new Error('global setup requires Playwright use.baseURL');
  const reaped = await reapOwnedE2EUsers(page.request, baseURL);
  if (reaped.reclaimed > 0 || reaped.deferred > 0) {
    console.warn(
      `[e2e-user-reaper] reclaimed=${reaped.reclaimed} deferred=${reaped.deferred} live=${reaped.live}`,
    );
  }

  await page.context().storageState({ path: STORAGE });
});
