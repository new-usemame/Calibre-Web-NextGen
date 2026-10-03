import { test, expect } from '@playwright/test';
import { assertNoHorizontalOverflow, assertNoPageErrors, collectPageErrors } from './utils';

test('Classic creation uses the same smart-shelf name as New UI', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app/magic');
  await expect(page.getByRole('main').getByRole('heading', { name: 'Smart shelves', exact: true })).toBeVisible();
  await page.goto('/magicshelf');
  await expect(page.getByRole('heading', { name: 'Create smart shelf', exact: true })).toBeVisible();
  await expect(page.locator('#nav_createmagicshelf')).toHaveText('Create smart shelf');
  await expect(page.getByText('What are smart shelves?', { exact: true })).toBeVisible();
  await page.getByText('What are smart shelves?', { exact: true }).click();
  await expect(page.getByText('Smart shelves were called Magic Shelves in older versions.', { exact: true })).toBeVisible();
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});

test('stored smart-shelf names and icons are text in Classic headings', async ({ page }, testInfo) => {
  // #1498: renaming the existing safe-HTML heading revealed stored markup injection.
  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.status()).toBe(200);
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const payload = '<img src="/not-a-shelf-image" onerror="window.__shelfProbe=1">';
  for (const field of ['name', 'icon'] as const) {
    const data = {
      name: `Heading ${testInfo.project.name} ${field} ${Date.now()}`,
      icon: '🪄',
      rules: { condition: 'AND', rules: [{ id: 'title', field: 'title', type: 'string', operator: 'contains', value: 'Native' }] },
      is_public: false,
    };
    data[field] = payload;
    const created = await page.request.post('/magicshelf', { headers, data });
    expect(created.status()).toBe(200);
    const result = await created.json() as { success: boolean; shelf_id: number };
    expect(result.success).toBe(true);
    try {
      await page.addInitScript(() => { (window as unknown as Record<string, unknown>).__shelfProbe = 0; });
      await page.goto(`/magicshelf/${result.shelf_id}`);
      const heading = page.locator('.discover.load-more h2');
      await expect(heading.locator('img')).toHaveCount(0);
      await expect(heading).toContainText(payload);
      await expect(heading).toContainText('Smart shelf —');
      expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__shelfProbe)).toBe(0);
    } finally {
      const removed = await page.request.post(`/magicshelf/${result.shelf_id}/delete`, { headers });
      expect(removed.status(), 'owned test shelf must be removed').toBe(200);
      expect((await removed.json()).success).toBe(true);
    }
  }
});
