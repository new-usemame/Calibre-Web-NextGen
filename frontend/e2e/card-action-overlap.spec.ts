import { test, expect } from '@playwright/test';

test('a Discover rail action opens outside the horizontally clipped rail', async ({ page }) => {
  test.skip(test.info().project.use.hasTouch === true, 'desktop keyboard/focus interaction');
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto('/app/');

  const discover = page.getByTestId('discover-section');
  await expect(discover).toBeVisible();
  const trigger = discover.getByRole('button', { name: /^Actions for / }).first();
  await expect(trigger).toBeVisible();
  await trigger.focus();
  await trigger.press('Enter');

  const dialog = page.getByRole('dialog', { name: /^Actions for / });
  await expect(dialog).toBeVisible();
  const isPortaledOutsideRail = await dialog.evaluate((node) => {
    const rail = document.querySelector('[data-testid="discover-section"]');
    return document.body.contains(node) && !rail?.contains(node);
  });
  expect(isPortaledOutsideRail, 'the action panel must escape the rail clipping container').toBe(true);

  const panel = await dialog.boundingBox();
  expect(panel).not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(panel!.x).toBeGreaterThanOrEqual(0);
  expect(panel!.y).toBeGreaterThanOrEqual(0);
  expect(panel!.x + panel!.width).toBeLessThanOrEqual(viewport.width);
  expect(panel!.y + panel!.height).toBeLessThanOrEqual(viewport.height);
  await expect(dialog.getByRole('link', { name: 'Read now', exact: true })).toBeVisible();
});
