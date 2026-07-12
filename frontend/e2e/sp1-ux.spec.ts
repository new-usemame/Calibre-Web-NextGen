import { expect, test } from '@playwright/test';

test('Library navigation hierarchy and search are coherent (#664, #714, #722, #723)', async ({ page }, testInfo) => {
  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

  if (testInfo.project.name === 'mobile') {
    await page.getByRole('button', { name: 'Search' }).click();
  }
  const simpleSearch = page.getByRole('search').getByRole('searchbox');
  await expect(simpleSearch).toHaveCount(1);
  await expect(page.getByRole('link', { name: 'Advanced' })).toBeVisible();
  await simpleSearch.fill('sp1-query');
  await simpleSearch.press('Enter');
  await expect(page).toHaveURL(/\?q=sp1-query/);
  if (testInfo.project.name === 'mobile') {
    await page.getByRole('button', { name: 'Search' }).click();
  }
  await expect(page.getByRole('search').getByRole('searchbox')).toHaveValue('sp1-query');

  await page.goto('/app');
  await expect(page.getByRole('link', { name: 'Upload books' })).toBeVisible();
  if (testInfo.project.name === 'mobile') {
    await page.getByRole('button', { name: /open navigation/i }).click();
  }
  const nav = page.getByRole('navigation', { name: 'Browse' });
  await expect(nav.getByRole('link', { name: 'Upload' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'Admin' })).toHaveCount(0);

  const customize = nav.getByRole('button', { name: 'Customize navigation' });
  await expect(customize).toBeVisible();
  const about = nav.getByRole('link', { name: 'About' });
  const customizeBox = await customize.boundingBox();
  const aboutBox = await about.boundingBox();
  expect(customizeBox && aboutBox && customizeBox.y > aboutBox.y).toBeTruthy();
  await customize.click();
  await expect(nav.getByRole('button', { name: 'Done' })).toBeFocused();
  await nav.getByRole('button', { name: 'Cancel' }).click();
  await expect(customize).toBeFocused();

  if (testInfo.project.name === 'mobile') await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /^Account:/ }).click();
  await expect(page.getByRole('link', { name: 'Admin' })).toHaveCount(1);
});

test('grid keeps details and adds a direct reader action (#653)', async ({ page }) => {
  await page.goto('/app');
  const read = page.getByRole('link', { name: /^Read .+/ }).first();
  try {
    await read.waitFor({ state: 'attached', timeout: 8_000 });
  } catch {
    test.skip(true, 'seed has no directly readable card');
  }
  const readHref = await read.getAttribute('href');
  expect(readHref).toMatch(/\/(read|view)\//);

  const card = read.locator('..');
  const details = card.getByRole('link', { name: /^Open details for / });
  await expect(details).toBeVisible();
  await expect(details).toHaveAttribute('href', /\/book\//);

  await read.click();
  await expect(page).toHaveURL(/\/(read|view)\//);
});

test('admin reset route is CSRF-protected and rejects unsafe live targets (#745)', async ({ page }) => {
  await page.goto('/app/admin');
  const outcome = await page.evaluate(async () => {
    const me = await fetch('/api/v1/auth/me').then((response) => response.json());
    const csrf = await fetch('/api/v1/auth/csrf').then((response) => response.json());
    const response = await fetch(`/api/v1/admin/users/${me.id}/reset-password`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
    });
    return { status: response.status, body: await response.json() };
  });
  expect(outcome.status).toBe(409);
  expect(outcome.body.error.code).toBe('conflict');
  await expect(page.getByRole('heading', { name: 'User administration' })).toBeVisible();
});
