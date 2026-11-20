import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

async function firstBook(page: Page): Promise<number> {
  const href = await page.getByTestId('catalog-grid').locator('a[href*="/book/"]').first().getAttribute('href');
  return Number(/\/book\/(\d+)$/.exec(href ?? '')?.[1]);
}

test('Read list sorts by completion date, remembers the choice, and resets when leaving Read', async ({ secondaryUser }, testInfo) => {
  const { page, context } = secondaryUser;
  const viewport = testInfo.project.use.viewport;
  if (viewport) await page.setViewportSize(viewport);
  const response = await context.request.get('/api/v1/books?sort=new&per_page=250');
  expect(response.ok()).toBeTruthy();
  const books = (await response.json()).items as { id: number }[];
  expect(books.length).toBeGreaterThanOrEqual(2);
  const newestAdded = books[0].id;
  const oldestAdded = books[books.length - 1].id;
  const csrf = (await (await context.request.get('/api/v1/auth/csrf')).json()).csrf_token as string;
  // Finish in the opposite order to date added. This account owns its history;
  // another test cannot add a finish or change its default library view.
  for (const id of [newestAdded, oldestAdded]) {
    const marked = await context.request.post(`/api/v1/books/${id}/read-status`, {
      headers: { 'X-CSRFToken': csrf }, data: { status: 'finished' },
    });
    expect(marked.ok(), await marked.text()).toBeTruthy();
  }
  await page.reload();
  const menu = page.getByRole('combobox', { name: 'Sort order' });
  await expect(menu.locator('option[value="readnew"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Read', exact: true }).click();
  await expect(menu.locator('option[value="new"]')).toHaveText('Added, newest first');
  await expect(menu.locator('option[value="old"]')).toHaveText('Added, oldest first');
  await menu.selectOption('readnew');
  await expect.poll(() => firstBook(page)).toBe(oldestAdded);
  await menu.selectOption('readold');
  await expect.poll(() => firstBook(page)).toBe(newestAdded);

  await page.reload();
  await expect(page.getByRole('button', { name: 'Read', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(menu).toHaveValue('readold');
  await expect.poll(() => firstBook(page)).toBe(newestAdded);
  await page.screenshot({ path: testInfo.outputPath('read-list.png'), fullPage: true });
  await testInfo.attach('Read list', { path: testInfo.outputPath('read-list.png'), contentType: 'image/png' });
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await expect(menu).toHaveValue('recent');
  await expect(menu.locator('option[value="readold"]')).toHaveCount(0);
  await expect(menu.locator('option[value="new"]')).toHaveText('Newest');
});
