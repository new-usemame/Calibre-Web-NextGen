import { test, expect } from '@playwright/test';

test('#666 book detail returns to the originating author list', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop project only');

  await page.goto('/app/authors');
  const firstAuthor = page.locator('a[href*="/authors/"]').first();
  await expect(firstAuthor).toBeVisible({ timeout: 20_000 });
  await firstAuthor.click();
  await expect(page).toHaveURL(/\/authors\/[^/?]+(?:\?.*)?$/);

  const originUrl = page.url();
  const originPath = new URL(originUrl).pathname;
  expect(originPath).not.toMatch(/\/app\/?$/);

  const firstBook = page.locator('main a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible({ timeout: 20_000 });
  await firstBook.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);

  const backLink = page.getByRole('link', { name: '← Back', exact: true });
  await expect(backLink).toBeVisible();
  await backLink.click();

  await expect(page).toHaveURL(originUrl);
  expect(new URL(page.url()).pathname).toBe(originPath);
  expect(new URL(page.url()).pathname).not.toMatch(/\/app\/?$/);
});
