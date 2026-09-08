// Same-document navigation is essential: page.goto/reload discards the cache
// whose stale cards these regressions protect. Setup alone uses direct APIs.
import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return { 'X-CSRFToken': (await response.json()).csrf_token };
}

async function catalog(page: Page): Promise<Array<{ id: number; title: string }>> {
  const response = await page.request.get('/api/v1/books?sort=new&per_page=200');
  expect(response.ok()).toBeTruthy();
  return (await response.json()).items;
}

const card = (page: Page, title: string) => page.getByTestId('catalog-grid')
  .getByRole('link', { name: `Open details for ${title}`, exact: true });

test('removing a book after visiting the catalog does not resurrect it on Back', async ({ page: admin, secondaryUser }) => {
  const page = secondaryUser.page;
  expect((await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
    headers: await csrf(admin),
    data: { roles: { browse_global: true }, library_mode: 'personal_library' },
  })).ok()).toBeTruthy();
  await page.goto('/app');
  const books = await catalog(page);
  expect(books.length).toBeGreaterThan(1);
  const book = books[0];
  await card(page, book.title).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Remove from my library', exact: true }).click();
  await expect(page.getByText('Removed from your library', { exact: true })).toBeAttached();
  await page.getByRole('link', { name: '← Library', exact: true }).click();
  expect((await catalog(page)).map(book => book.id)).not.toContain(book.id);
  await expect(card(page, book.title)).toHaveCount(0);
});

test('switching from whole library to a saved selection clears earlier catalog cards', async ({ page: admin, secondaryUser }) => {
  const page = secondaryUser.page;
  expect((await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
    headers: await csrf(admin),
    data: { roles: { browse_global: true }, library_mode: 'personal_library' },
  })).ok()).toBeTruthy();
  const books = await catalog(page);
  expect(books.length).toBeGreaterThan(1);
  const book = books[0];
  expect((await page.request.delete(`/api/v1/books/${book.id}/my-library`, {
    headers: await csrf(page),
  })).ok()).toBeTruthy();
  expect((await page.request.post('/api/v1/account/library-mode', {
    headers: await csrf(page), data: { mode: 'monolibrary' },
  })).ok()).toBeTruthy();
  await page.goto('/app');
  await expect(card(page, book.title)).toBeVisible();
  await page.getByRole('button', { name: `Account: ${secondaryUser.username}` }).click();
  await page.getByRole('link', { name: 'My account', exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('radio', { name: /My Library/ }).click();
  await expect(page.getByRole('radio', { name: /My Library/ })).toBeChecked();
  const menu = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('link', { name: 'My Library', exact: true }).first().click();
  expect((await catalog(page)).map(book => book.id)).not.toContain(book.id);
  await expect(card(page, book.title)).toHaveCount(0);
});
