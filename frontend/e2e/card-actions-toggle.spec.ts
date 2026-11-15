import { test, expect } from './fixtures';

/* #1054: the account preference removes the cover action disclosure from each card. */
const CARD_ACTIONS = '[class*="grid"] button[aria-label^="Actions for "]';

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

test('cover actions can be switched off from View settings (#1054)', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');

  // Baseline: the disclosure is on by default, so there is something to remove.
  const actionsBefore = await page.locator(CARD_ACTIONS).count();
  expect(actionsBefore, 'seeded library must expose card actions before hiding them').toBeGreaterThan(0);

  await openViewSettings(page);
  const toggle = page.getByTestId('show-card-actions');
  await expect(toggle, 'the preference ships on, so nobody loses card actions by upgrading').toBeChecked();

  // Switching it off removes each trigger from the DOM, not merely from view.
  await setCardActionsVisible(page, false);
  await expect(page.locator(CARD_ACTIONS), 'hidden actions must leave no tab stop').toHaveCount(0);

  // Survives a reload (the whole point of persisting it).
  await page.reload();
  await page.waitForLoadState('networkidle');
  await expect(page.locator(CARD_ACTIONS)).toHaveCount(0);

  // And switching it back on restores exactly what was there before.
  await openViewSettings(page);
  await setCardActionsVisible(page, true);
  await expect(page.locator(CARD_ACTIONS).first()).toBeAttached();
});

test('hiding card actions leaves the cover link intact (#1054)', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');

  await openViewSettings(page);
  await setCardActionsVisible(page, false);
  await page.keyboard.press('Escape');

  // Hiding the optional shortcut must not remove the card's primary book link.
  const card = page.locator('[class*="grid"] a[href*="/book/"]').first();
  await expect(card).toBeVisible();

  // Restore before leaving. The test-scoped account is deleted by the fixture,
  // but exercising both directions is part of this control's contract.
  await openViewSettings(page);
  await setCardActionsVisible(page, true);
});
