import { test, expect } from '@playwright/test';

// Fork #640 — the Library view's sort order and read-status filter must survive
// a full page reload (F5), not just client-side back/forward. Before the fix the
// state lived only in the in-memory scrollCache, so a reload reset sort → "Newest"
// and the read filter → "All". These specs pin the persisted-across-reload contract
// for the plain Library view only (entity/series/special views keep contextual
// defaults per #573/#498 and are intentionally not persisted).

test('Library sort order persists across a full page reload', async ({ page }) => {
  await page.goto('/app');
  const sort = page.getByRole('combobox', { name: 'Sort order' });
  await expect(sort).toBeVisible();

  await sort.selectOption({ label: 'Author A–Z' });
  await expect(sort).toHaveValue('authaz');
  expect(await page.evaluate(() => localStorage.getItem('cwng:library-sort-v2'))).toBe('authaz');

  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Sort order' })).toHaveValue('authaz');
});

test('Library read-status filter persists across a full page reload', async ({ page }) => {
  await page.goto('/app');
  const unread = page.getByRole('button', { name: 'Unread', exact: true });
  await expect(unread).toBeVisible();

  await unread.click();
  await expect(unread).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => localStorage.getItem('cwng:library-readfilter-v1'))).toBe('unread');

  await page.reload();
  await expect(page.getByRole('button', { name: 'Unread', exact: true })).toHaveAttribute('aria-pressed', 'true');
});
