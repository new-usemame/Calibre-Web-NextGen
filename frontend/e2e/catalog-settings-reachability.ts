import { expect, type Page } from '@playwright/test';

export async function expectSettingsReachable(page: Page, width: number) {
  await page.goto('/app');
  await expect(page.getByTestId('catalog-grid')).toBeVisible();
  const trigger = page.getByTestId('catalog-view-settings');
  await trigger.click();
  const panel = page.getByTestId('catalog-view-settings-menu');
  await expect(panel).toBeVisible();
  await expect.poll(async () => (await panel.boundingBox())!.x).toBeGreaterThanOrEqual(0);
  const box = (await panel.boundingBox())!;
  const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
  expect(box.x + box.width).toBeLessThanOrEqual(Math.min(width, clientWidth));
  const triggerBox = (await trigger.boundingBox())!;
  expect(box.x).toBeLessThanOrEqual(triggerBox.x + triggerBox.width + 16);
  expect(box.x + box.width).toBeGreaterThanOrEqual(triggerBox.x - 16);
  const last = panel.getByRole('radio').last();
  await last.focus();
  await expect(last).toBeInViewport();
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(trigger).toBeFocused();
}

