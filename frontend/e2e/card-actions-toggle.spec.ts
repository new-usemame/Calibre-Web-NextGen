import { test, expect } from './fixtures';

/*
 * #1054 — "Can we please have the option of hiding the 'Read Now' and the edit
 * button in Library view. It makes the main page look messy." (@Glennza1962)
 *
 * The controls were already hover-revealed on a mouse (#1185), and were pinned
 * visible on touch by the coarse-pointer block when this preference shipped.
 * The 2026-08-29 operator ruling reversed that touch default, but the
 * preference is still the thing a user who NEVER wants them reaches for: it
 * removes the row entirely rather than merely hiding it. Persisted per account,
 * with a guest/local fallback.
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

async function setCardActionsVisible(
  page: import('@playwright/test').Page,
  visible: boolean,
) {
  const toggle = page.getByTestId('show-card-actions');
  const saved = page.waitForResponse((response) =>
    response.url().includes('/api/v1/account/preferences')
    && response.request().method() === 'POST');
  await toggle.click();
  expect((await saved).ok()).toBeTruthy();
  if (visible) await expect(toggle).toBeChecked();
  else await expect(toggle).not.toBeChecked();
}

test('the Read now + edit row can be switched off from View settings (#1054)', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
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

  // Switch it off. count() counts elements that are merely transparent or
  // display:none too, so zero here means they left the DOM — which is the
  // tab-order guarantee, not just a visual one.
  await setCardActionsVisible(page, false);
  await expect(page.locator(READ_NOW),
    'the read link must leave the DOM, not just go transparent').toHaveCount(0);
  await expect(page.locator(PENCIL),
    'the edit pencil must leave the DOM, not just go transparent').toHaveCount(0);

  // Survives a reload (the whole point of persisting it).
  await page.reload();
  await page.waitForLoadState('networkidle');
  await expect(page.locator(READ_NOW)).toHaveCount(0);
  await expect(page.locator(PENCIL)).toHaveCount(0);

  // And switching it back on restores exactly what was there before.
  await openViewSettings(page);
  await setCardActionsVisible(page, true);
  await expect.poll(async () =>
    await page.locator(READ_NOW).count() + await page.locator(PENCIL).count(),
  ).toBeGreaterThan(0);
});

test('hiding card actions leaves the cover link intact (#1054)', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');

  await openViewSettings(page);
  await setCardActionsVisible(page, false);
  await page.keyboard.press('Escape');

  // Both actions still have a home: the cover links to the book page, which
  // carries Read and Edit. Losing that would make the preference a trap.
  // href*=, not href^=: the SPA is served under the /app base path, so the
  // rendered link is /app/book/<id>. With the action row off this is the only
  // remaining /book/ link on a card, i.e. the cover itself.
  const card = page.locator('[class*="grid"] a[href*="/book/"]').first();
  await expect(card).toBeVisible();

  // Restore before leaving. The test-scoped account is deleted by the fixture,
  // but exercising both directions is part of this control's contract.
  await openViewSettings(page);
  await setCardActionsVisible(page, true);
});
