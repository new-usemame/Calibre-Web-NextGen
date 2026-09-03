import { test, expect, type Page, type Locator } from '@playwright/test';

/*
 * The server-wide "Try My Library" intro card on /app/admin.
 *
 * LANE: this spec flips every non-guest account's mode at once, so it lives in
 * the env-gated `server-state-chromium` project (playwright.config.ts) and runs
 * as its own invocation — `E2E_SERVER_STATE=1 npx playwright test
 * --project=server-state-chromium` — never interleaved with the parallel lanes.
 *
 * State hygiene: the spec asserts through the intro endpoint's own payload
 * (snapshot_accounts / restored_accounts) rather than other users' rows, and
 * ALWAYS ends at not_enabled via a finally-guarded undo (which also clears
 * dismissal). Per-account snapshot/restore semantics (role bits both
 * directions, dormant selections, Guest exclusion) are owned by
 * tests/unit/test_my_library_admin_intro.py. A crashed run self-heals: the
 * arrange step undoes leftover enabled state.
 */

async function csrf(page: import('@playwright/test').Page) {
  const res = await page.request.get('/api/v1/auth/csrf');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function introState(page: import('@playwright/test').Page) {
  const res = await page.request.get('/api/v1/admin/my-library/intro');
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as {
    status: string; dismissed: boolean; snapshot_accounts: number;
  };
}

async function undoIfEnabled(page: import('@playwright/test').Page) {
  if ((await introState(page)).status !== 'enabled') return;
  const res = await page.request.post('/api/v1/admin/my-library/intro/undo', {
    headers: { 'X-CSRFToken': await csrf(page) },
  });
  expect(res.ok()).toBeTruthy();
}

async function tryMyLibrary(page: Page, card: Locator) {
  const confirmed = page.waitForEvent('dialog').then(async (dialog) => {
    expect(dialog.message()).toContain('Each account starts with every book it can currently see');
    await dialog.accept();
  });
  await Promise.all([confirmed, card.getByRole('button', { name: 'Try My Library' }).click()]);
}

test.describe('My Library admin intro card', () => {
  test('try → enabled with undo, undo restores, close dismisses permanently', async ({ page }) => {
    await undoIfEnabled(page);

    try {
      await page.goto('/app/admin');
      const card = page.getByRole('region', { name: 'New Feature!' });
      await expect(card).toBeVisible();

      // NOT-ENABLED: full pitch, disabled Undo preview, NO close affordance.
      await expect(card.getByRole('button', { name: 'Try My Library' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Undo' })).toBeDisabled();
      await expect(card.getByRole('button', { name: 'Close' })).toHaveCount(0);
      await expect(card.getByRole('button', { name: 'Dismiss introduction' })).toHaveCount(0);

      // Try → ENABLED: copy swaps, Undo activates, x-mark appears, and the
      // server reports a snapshot covering every non-guest account.
      // Try is guarded by a native confirm naming the seed rule; Playwright
      // dismisses dialogs by default, which would silently skip the enable.
      await tryMyLibrary(page, card);
      await expect(card).toContainText('Explore the changes, you can always undo later.');
      await expect(card.getByRole('button', { name: 'Undo' })).toBeEnabled();
      await expect(card.getByRole('button', { name: 'Close' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Dismiss introduction' })).toBeVisible();
      const enabled = await introState(page);
      expect(enabled.status).toBe('enabled');
      expect(enabled.snapshot_accounts).toBeGreaterThan(0);

      // Undo → NOT-ENABLED again, snapshot consumed, close affordances gone.
      await card.getByRole('button', { name: 'Undo' }).click();
      await expect(card.getByRole('button', { name: 'Try My Library' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Close' })).toHaveCount(0);
      const undone = await introState(page);
      expect(undone).toMatchObject({ status: 'not_enabled', dismissed: false, snapshot_accounts: 0 });

      // Enable once more, then Close dismisses permanently (survives reload).
      // Try is guarded by a native confirm naming the seed rule; Playwright
      // dismisses dialogs by default, which would silently skip the enable.
      await tryMyLibrary(page, card);
      await expect(card).toContainText('Explore the changes, you can always undo later.');
      await card.getByRole('button', { name: 'Close' }).click();
      await expect(page.getByRole('region', { name: 'New Feature!' })).toHaveCount(0);
      await page.reload();
      await expect(page.getByRole('region', { name: 'New Feature!' })).toHaveCount(0);
      expect((await introState(page)).dismissed).toBe(true);
    } finally {
      // Restore the shared default for the rest of the suite: not_enabled,
      // undismissed, every account's prior mode/role restored server-side.
      await undoIfEnabled(page);
      expect(await introState(page))
        .toMatchObject({ status: 'not_enabled', dismissed: false });
    }
  });
});
