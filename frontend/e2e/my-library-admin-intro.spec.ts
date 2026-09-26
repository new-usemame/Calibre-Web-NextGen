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
  if (!['enabled', 'incomplete'].includes((await introState(page)).status)) return;
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

// UI contract for the persisted partial response. Durable failure/restart and
// failed-only retry are exercised against real SQLite in the backend suite.
for (const action of ['Retry', 'Undo'] as const) {
  test(`incomplete setup exposes ${action} and cannot be dismissed`, async ({ page }) => {
    await undoIfEnabled(page);
    if (action === 'Undo') {
      const enabled = await page.request.post('/api/v1/admin/my-library/intro/enable', {
        headers: { 'X-CSRFToken': await csrf(page) },
      });
      expect(enabled.ok()).toBeTruthy();
    }
    let attempted = false;
    const pending = {
      status: 'incomplete', dismissed: false, snapshot_accounts: 2,
      pending_accounts: 1,
      failed_accounts: [{ user_id: 999999, name: 'Interrupted reader', error: 'Temporary seed failure' }],
    };
    await page.route('**/api/v1/admin/my-library/intro', async route => {
      if (attempted) return route.continue();
      await route.fulfill({ json: pending });
    });
    const path = `/api/v1/admin/my-library/intro/${action === 'Retry' ? 'enable' : 'undo'}`;
    await page.route(`**${path}`, async route => {
      attempted = true;
      // Exercise real action/CSRF/session, then real returned state and me refresh.
      await route.continue();
    });
    try {
      await page.goto('/app/admin');
      const card = page.getByRole('region', { name: 'New Feature!' });
      await expect(card).toContainText('incomplete for 1 account(s)');
      await expect(card).toContainText('Interrupted reader: Temporary seed failure');
      await expect(card.getByRole('button', { name: 'Close', exact: true })).toHaveCount(0);
      await expect(card.getByRole('button', { name: 'Dismiss introduction' })).toHaveCount(0);
      await expect(card.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
      await expect(card.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
      const response = page.waitForResponse(r => r.url().endsWith(path) && r.request().method() === 'POST');
      await card.getByRole('button', { name: action, exact: true }).click();
      expect((await response).ok()).toBeTruthy();
      await expect(card).not.toContainText('Temporary seed failure');
      if (action === 'Retry') await expect(card.getByRole('button', { name: 'Close', exact: true })).toBeVisible();
      else await expect(card.getByRole('button', { name: 'Try My Library', exact: true })).toBeVisible();
    } finally {
      await page.unrouteAll({ behavior: 'wait' });
      await undoIfEnabled(page);
    }
  });
}
