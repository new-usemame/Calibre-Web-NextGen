import { expect, test, type Page } from '@playwright/test';

/*
 * #1902: the classic Duplicates page's "Auto-Resolve Duplicates" panel must exist
 * in the new UI. The API is mocked so the spec needs no duplicate books (and can
 * never delete real ones); the signed-in e2e account is the library admin.
 */
const GROUP = {
  group_hash: 'g1', title: 'Dune', author: 'Frank Herbert', count: 2,
  books: [
    { id: 7, title: 'Dune', authors: 'Frank Herbert', formats: ['MOBI'], cover_url: null },
    { id: 12, title: 'Dune', authors: 'Frank Herbert', formats: ['EPUB', 'PDF'], cover_url: null },
  ],
};

const PLAN = {
  success: true, resolved_count: 1, kept_count: 1, deleted_count: 1,
  preview: [{
    group_hash: 'g1', title: 'Dune', author: 'Frank Herbert',
    kept_book_id: 12, kept_book_formats: ['EPUB', 'PDF'],
    deleted_book_ids: [7], deleted_books_info: [{ id: 7, formats: ['MOBI'] }],
  }],
};

async function mockDuplicates(page: Page, calls: { preview: unknown[]; execute: unknown[] }) {
  await page.route('**/api/v1/duplicates', (route) =>
    route.fulfill({ json: { items: [GROUP], needs_scan: false } }));
  await page.route('**/duplicates/preview-resolution', (route) => {
    calls.preview.push(route.request().postDataJSON());
    return route.fulfill({ json: PLAN });
  });
  await page.route('**/duplicates/execute-resolution', (route) => {
    calls.execute.push(route.request().postDataJSON());
    return route.fulfill({ json: { success: true, resolved_count: 1, kept_count: 1, deleted_count: 1, errors: [] } });
  });
}

test('admin can preview, confirm and run an automatic duplicate resolution', async ({ page }) => {
  const calls = { preview: [] as unknown[], execute: [] as unknown[] };
  await mockDuplicates(page, calls);
  await page.goto('/app/duplicates');

  await expect(page.getByRole('heading', { name: 'Resolve automatically' })).toBeVisible();
  await page.getByLabel('Strategy').selectOption('oldest');
  await page.getByRole('button', { name: 'Preview' }).click();

  await expect(page.getByText('1 books in 1 groups would be deleted.')).toBeVisible();
  expect(calls.preview).toEqual([{ strategy: 'oldest' }]);
  expect(calls.execute, 'a preview must never delete anything').toEqual([]);

  await page.getByRole('button', { name: 'Delete duplicates' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(calls.execute, 'cancelling the confirmation must not delete').toEqual([]);

  await page.getByRole('button', { name: 'Delete duplicates' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete duplicates' }).click();
  await expect(page.getByText('Resolved 1 groups and deleted 1 books.')).toBeVisible();
  expect(calls.execute).toEqual([{ strategy: 'oldest' }]);
});

test('changing the strategy discards a stale preview', async ({ page }) => {
  const calls = { preview: [] as unknown[], execute: [] as unknown[] };
  await mockDuplicates(page, calls);
  await page.goto('/app/duplicates');

  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByRole('button', { name: 'Delete duplicates' })).toBeVisible();
  await page.getByLabel('Strategy').selectOption('merge');
  await expect(page.getByRole('button', { name: 'Delete duplicates' })).toHaveCount(0);
});
