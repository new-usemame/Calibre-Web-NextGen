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
    kept_book_edition: { publisher: 'Penguin Classics', year: 2003 },
    deleted_book_ids: [7], deleted_books_info: [{ id: 7, formats: ['MOBI'], publisher: 'OUP', year: 1991 }],
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
  let shelfReads = 0;
  await page.route('**/api/v1/shelves', (route) => {
    shelfReads++;
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto('/app/duplicates');

  await expect(page.getByRole('heading', { name: 'Resolve automatically' })).toBeVisible();
  await expect.poll(() => shelfReads).toBeGreaterThan(0);
  const initialShelfReads = shelfReads;
  await page.getByLabel('Strategy').selectOption('oldest');
  await page.getByRole('button', { name: 'Preview' }).click();

  await expect(page.getByText('1 books in 1 groups would be deleted.')).toBeVisible();
  // Publisher and year are shown so different editions stand out from true copies.
  await expect(page.getByText('#12 (EPUB, PDF, Penguin Classics, 2003)')).toBeVisible();
  await expect(page.getByText('#7 (MOBI, OUP, 1991)')).toBeVisible();
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
  expect(calls.execute).toEqual([{ strategy: 'oldest', expected_preview: PLAN.preview }]);
  await expect.poll(() => shelfReads, 'bulk deletion refreshes shelf counts as well as the catalog')
    .toBeGreaterThan(initialShelfReads);
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

test('a pending preview locks its strategy until the response arrives', async ({ page }) => {
  // A slow preview must not offer a newest plan beside an oldest execution.
  const calls = { preview: [] as unknown[], execute: [] as unknown[] };
  await mockDuplicates(page, calls);
  let release!: () => void;
  const responseReady = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/duplicates/preview-resolution', async (route) => {
    calls.preview.push(route.request().postDataJSON());
    await responseReady;
    await route.fulfill({ json: PLAN });
  });
  await page.goto('/app/duplicates');
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect.poll(() => calls.preview.length).toBe(1);
  try {
    await expect(page.getByLabel('Strategy')).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Delete duplicates' })).toHaveCount(0);
  } finally {
    release();
  }
  await expect(page.getByRole('button', { name: 'Delete duplicates' })).toBeVisible();
  await expect(page.getByLabel('Strategy')).toBeEnabled();
});

test('every edition remains inspectable in a preview larger than fifty groups', async ({ page }) => {
  const calls = { preview: [] as unknown[], execute: [] as unknown[] };
  await mockDuplicates(page, calls);
  await page.route('**/duplicates/preview-resolution', (route) => route.fulfill({ json: {
    ...PLAN, deleted_count: 51, resolved_count: 51,
    preview: Array.from({ length: 51 }, (_, i) => ({
      ...PLAN.preview[0], group_hash: `g${i}`, title: `Edition group ${i + 1}`,
    })),
  } }));
  await page.goto('/app/duplicates');
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByText('Edition group 51', { exact: true })).toBeAttached();
  expect(calls.execute).toEqual([]);
});

test('Escape cannot dismiss a deletion in progress and start another run', async ({ page }) => {
  const calls = { preview: [] as unknown[], execute: [] as unknown[] };
  await mockDuplicates(page, calls);
  let release!: () => void;
  const responseReady = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/duplicates/execute-resolution', async (route) => {
    calls.execute.push(route.request().postDataJSON());
    await responseReady;
    await route.fulfill({ json: { success: true, resolved_count: 1, deleted_count: 1 } });
  });
  await page.goto('/app/duplicates');
  await page.getByRole('button', { name: 'Preview' }).click();
  await page.getByRole('button', { name: 'Delete duplicates' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete duplicates' }).click();
  await expect.poll(() => calls.execute.length).toBe(1);
  try {
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('dialog').getByRole('button', { name: 'Cancel' })).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
