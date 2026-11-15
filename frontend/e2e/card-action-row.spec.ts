import { test, expect } from '@playwright/test';

test('the cover disclosure and action dialog remain usable in the narrowest grid', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('cwng:catalog-density-v1', 'dense'));
  await page.setViewportSize({ width: 280, height: 800 });
  await page.goto('/app/');

  const grid = page.getByTestId('catalog-grid');
  await expect(grid).toHaveClass(/density_dense/);
  const details = grid.locator('a[aria-label^="Open details for"]').first();
  await expect(details).toBeVisible();
  const card = details.locator('..');
  const trigger = card.getByRole('button', { name: /^Actions for / });
  const cover = card.locator('[class*="coverWrap"]');
  const coverBox = await cover.boundingBox();
  const triggerBox = await trigger.boundingBox();
  expect(coverBox).not.toBeNull();
  expect(triggerBox).not.toBeNull();
  expect(triggerBox!.width).toBeGreaterThanOrEqual(32);
  expect(triggerBox!.height).toBeGreaterThanOrEqual(32);
  expect(triggerBox!.x).toBeGreaterThanOrEqual(coverBox!.x);
  expect(triggerBox!.y).toBeGreaterThanOrEqual(coverBox!.y);
  expect(triggerBox!.x + triggerBox!.width).toBeLessThanOrEqual(coverBox!.x + coverBox!.width);
  expect(triggerBox!.y + triggerBox!.height).toBeLessThanOrEqual(coverBox!.y + coverBox!.height);

  await trigger.focus();
  await trigger.press('Enter');
  const dialog = page.getByRole('dialog', { name: /^Actions for / });
  await expect(dialog).toBeVisible();
  const panel = await dialog.boundingBox();
  expect(panel).not.toBeNull();
  expect(panel!.x).toBeGreaterThanOrEqual(0);
  expect(panel!.x + panel!.width).toBeLessThanOrEqual(280);
  expect(panel!.y).toBeGreaterThanOrEqual(0);
  expect(panel!.y + panel!.height).toBeLessThanOrEqual(800);
  await expect(dialog.getByRole('link', { name: 'Read now', exact: true })).toBeVisible();
});
