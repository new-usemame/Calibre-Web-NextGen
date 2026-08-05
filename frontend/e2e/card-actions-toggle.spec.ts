import { test, expect } from '@playwright/test';

/*
 * #1054 — "Can we please have the option of hiding the 'Read Now' and the edit
 * button in Library view. It makes the main page look messy." (@Glennza1962)
 *
 * The controls were already hover-revealed on a mouse (#1185), but a user who
 * reads on an ereader never wants them, and on a touchscreen they are pinned
 * visible by the coarse-pointer block. So: a real preference in the catalog's
 * View settings, persisted per browser.
 *
 * The load-bearing assertion is that switching it off REMOVES the controls
 * rather than hiding them. The hover-reveal uses `opacity: 0`, which leaves a
 * focusable link in the tab order — a keyboard user who turned these off would
 * otherwise tab through two invisible controls on every card in the grid.
 */

const READ_NOW = '[class*="grid"] a[class*="readNow"]';
const PENCIL = '[class*="grid"] a[class*="quickEditBtn"]';

async function openViewSettings(page: import('@playwright/test').Page) {
  await page.getByTestId('catalog-view-settings').click();
  await expect(page.getByTestId('catalog-view-settings-menu')).toBeVisible();
}

test('the Read now + edit row can be switched off from View settings (#1054)', async ({ page }) => {
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');

  // Baseline: the row is on by default, so there is something to remove.
  const readNowBefore = await page.locator(READ_NOW).count();
  const pencilBefore = await page.locator(PENCIL).count();
  test.skip(readNowBefore + pencilBefore === 0,
    'no card in this seed renders an action control');

  await openViewSettings(page);
  const toggle = page.getByTestId('show-card-actions');
  await expect(toggle, 'the preference ships on, so nobody loses the row by upgrading').toBeChecked();

  // Switch it off.
  await toggle.uncheck();
  await expect(page.locator(READ_NOW)).toHaveCount(0);
  await expect(page.locator(PENCIL)).toHaveCount(0);

  // Removed from the DOM, not merely transparent — otherwise these stay in the
  // tab order for a user who explicitly turned them off.
  const stillFocusable = await page.locator(`${READ_NOW}, ${PENCIL}`).count();
  expect(stillFocusable, 'action controls must leave the tab order, not just go transparent').toBe(0);

  // Survives a reload (the whole point of persisting it).
  await page.reload();
  await page.waitForLoadState('networkidle');
  await expect(page.locator(READ_NOW)).toHaveCount(0);
  await expect(page.locator(PENCIL)).toHaveCount(0);

  // And switching it back on restores exactly what was there before.
  await openViewSettings(page);
  await page.getByTestId('show-card-actions').check();
  await expect(page.locator(READ_NOW)).toHaveCount(readNowBefore);
  await expect(page.locator(PENCIL)).toHaveCount(pencilBefore);
});

test('hiding card actions leaves the cover link intact (#1054)', async ({ page }) => {
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');

  await openViewSettings(page);
  await page.getByTestId('show-card-actions').uncheck();
  await page.keyboard.press('Escape');

  // Both actions still have a home: the cover links to the book page, which
  // carries Read and Edit. Losing that would make the preference a trap.
  // href*=, not href^=: the SPA is served under the /app base path, so the
  // rendered link is /app/book/<id>. With the action row off this is the only
  // remaining /book/ link on a card, i.e. the cover itself.
  const card = page.locator('[class*="grid"] a[href*="/book/"]').first();
  await expect(card).toBeVisible();

  // Restore before leaving. Each test gets its own context, so this cannot leak
  // into another spec — but the toggle is the thing under test and leaving it
  // off would make a failure here read as a failure over there.
  await openViewSettings(page);
  await page.getByTestId('show-card-actions').check();
});
