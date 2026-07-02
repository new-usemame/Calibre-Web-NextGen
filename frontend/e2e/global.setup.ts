import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';

/*
 * Log in once via the real UI (exercises the CSRF+session flow users hit) and
 * persist the session so authed specs don't re-login. Default creds are the
 * cwn-local seed (admin / admin123); override with E2E_USER / E2E_PASS.
 */
const STORAGE = 'e2e/.auth/state.json';
const USER = process.env.E2E_USER || 'admin';
const PASS = process.env.E2E_PASS || 'admin123';

setup('authenticate', async ({ page }) => {
  fs.mkdirSync('e2e/.auth', { recursive: true });

  // The SPA renders its own login client-side at /app (the bare /login route is
  // the legacy Jinja page). Go to the SPA shell and drive its login form.
  await page.goto('/app');
  await page.locator('input[autocomplete="username"]').fill(USER);
  await page.locator('input[autocomplete="current-password"]').fill(PASS);
  await page.getByRole('button', { name: /sign in/i }).click();

  // Success = we leave the login route and the authed shell renders a book link.
  await expect(page).toHaveURL(/\/app(\/|$|\?)/, { timeout: 20_000 });
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible({ timeout: 20_000 });

  await page.context().storageState({ path: STORAGE });
});
