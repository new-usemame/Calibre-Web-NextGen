import { test, expect } from '@playwright/test';

test('#666 book back link returns to the immediately preceding author list', async ({ page }, testInfo) => {
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

test('#666 browser Back preserves the author origin after visiting sidebar Tags', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop project only');

  await page.goto('/app/authors');
  const firstAuthor = page.locator('a[href*="/authors/"]').first();
  await expect(firstAuthor).toBeVisible({ timeout: 20_000 });
  await firstAuthor.click();
  await expect(page).toHaveURL(/\/authors\/[^/?]+(?:\?.*)?$/);

  const originUrl = page.url();
  const parsedOrigin = new URL(originUrl);
  const originHref = `${parsedOrigin.pathname}${parsedOrigin.search}`;

  const firstBook = page.locator('main a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible({ timeout: 20_000 });
  await firstBook.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);
  const bookUrl = page.url();

  const tagsLink = page.getByRole('navigation', { name: 'Browse' })
    .getByRole('link', { name: 'Tags', exact: true });
  await expect(tagsLink).toBeVisible();
  await tagsLink.click();
  await expect(page).toHaveURL(/\/tags\/?(?:\?.*)?$/);
  const tagHref = `${new URL(page.url()).pathname}${new URL(page.url()).search}`;

  await page.goBack();
  await expect(page).toHaveURL(bookUrl);

  const backLink = page.getByRole('link', { name: '← Back', exact: true });
  await expect(backLink).toHaveAttribute('href', originHref);
  await expect(backLink).not.toHaveAttribute('href', tagHref);
  await backLink.click();
  await expect(page).toHaveURL(originUrl);
});

test('#666 cached same-book revisit stamps its back link during render', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop project only');

  await page.goto('/app/authors');
  const firstAuthor = page.locator('a[href*="/authors/"]').first();
  await expect(firstAuthor).toBeVisible({ timeout: 20_000 });
  await firstAuthor.click();
  await expect(page).toHaveURL(/\/authors\/[^/?]+(?:\?.*)?$/);

  const originUrl = page.url();
  const parsedOrigin = new URL(originUrl);
  const originHref = `${parsedOrigin.pathname}${parsedOrigin.search}`;

  const firstBook = page.locator('main a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible({ timeout: 20_000 });
  const bookHref = await firstBook.getAttribute('href');
  expect(bookHref).not.toBeNull();
  await firstBook.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);

  await page.getByRole('link', { name: '← Back', exact: true }).click();
  await expect(page).toHaveURL(originUrl);

  const sameBook = page.locator(`main a[href="${bookHref}"]`).first();
  await expect(sameBook).toBeVisible();
  await sameBook.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);

  const revisitBack = page.getByRole('link', { name: '← Back', exact: true });
  expect(await revisitBack.getAttribute('href')).toBe(originHref);
  await page.waitForTimeout(2_500);
  await expect(revisitBack).toHaveAttribute('href', originHref);
});
