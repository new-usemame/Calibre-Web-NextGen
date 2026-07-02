import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow } from './utils';

/*
 * The core user flow across desktop + mobile: land on the grid → open a book →
 * open the reader. This is the sequence where the v4.1.1 (subpath/stale-card),
 * #288/#576 (mobile), and #1411 (scroll) Class-1 bugs shipped — verified as a
 * full flow, not a function.
 */

test('grid loads with books and a clean console', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  expect(await page.locator('a[href*="/book/"]').count()).toBeGreaterThan(0);
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});

test('no modal/dialog is open by default on load (Class 5 default-state)', async ({ page }) => {
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  // Nothing gated should render in the default state (the v4.1.3 popup lesson).
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('open a book detail from the grid', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);
  await expect(page.getByRole('heading').first()).toBeVisible();
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});

test('open the reader for a book', async ({ page }) => {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);

  const readLink = page.locator('a[href*="/read/"]').first();
  if (await readLink.count() === 0) {
    test.skip(true, 'first book has no readable format in this seed');
  }
  await readLink.click();
  await expect(page).toHaveURL(/\/read\//);
  // epub.js renders into an iframe; its presence = the reader mounted.
  await expect(page.locator('iframe').first()).toBeVisible({ timeout: 20_000 });
});
