import { expect, Page, test } from '@playwright/test';

interface SeedBook {
  id: number;
  title: string;
}

async function firstBook(page: Page): Promise<SeedBook | null> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', {
      headers: { Accept: 'application/json' },
    }).catch(() => null);
    if (!response?.ok) return null;
    const book = (await response.json())?.items?.[0];
    return book ? { id: book.id, title: book.title } : null;
  });
}

async function setDeletePermission(page: Page, allowed: boolean) {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.role = { ...(me.role ?? {}), delete_books: allowed };
    await route.fulfill({ response, json: me });
  });
}

test('the edit page exposes whole-book deletion to users with delete permission (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}/edit`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toHaveAccessibleName('Delete book');
});

test('the edit page hides whole-book deletion without delete permission (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, false);
  await page.goto(`/app/book/${book.id}/edit`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete book' })).toHaveCount(0);
});

test('edit-page deletion confirms before mutation and a declined confirm does nothing (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  let deleteCalls = 0;
  await page.route(`**/api/v1/books/${book.id}/delete`, async (route) => {
    deleteCalls += 1;
    await route.fulfill({ status: 204, contentType: 'application/json', body: '' });
  });

  await page.goto(`/app/book/${book.id}/edit`, { waitUntil: 'domcontentloaded' });
  const deleteButton = page.getByTestId('edit-book-delete');
  await expect(deleteButton).toBeVisible();

  let declinedPrompt = '';
  page.once('dialog', (dialog) => {
    declinedPrompt = dialog.message();
    void dialog.dismiss();
  });
  await deleteButton.click();
  await page.waitForTimeout(500);

  expect(declinedPrompt).toContain(`"${book.title}"`);
  expect(declinedPrompt).toContain('cannot be undone');
  expect(deleteCalls, 'declining confirmation must not call the delete endpoint').toBe(0);
  await expect(page).toHaveURL(new RegExp(`/book/${book.id}/edit\\b`));

  let acceptedPrompt = '';
  page.once('dialog', (dialog) => {
    acceptedPrompt = dialog.message();
    void dialog.accept();
  });
  const [request] = await Promise.all([
    page.waitForRequest(`**/api/v1/books/${book.id}/delete`),
    deleteButton.click(),
  ]);

  expect(acceptedPrompt).toContain(`"${book.title}"`);
  expect(acceptedPrompt).toContain('cannot be undone');
  expect(request.method()).toBe('POST');
  await expect.poll(() => deleteCalls).toBe(1);
  await expect(page).not.toHaveURL(new RegExp(`/book/${book.id}(?:/edit)?\\b`));
});

test('book-detail deletion is grouped outside the ordinary action chips (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  const ordinaryActions = page.getByTestId('book-actions');
  const destructiveActions = page.getByTestId('book-destructive-actions');
  await expect(ordinaryActions).toBeVisible();
  await expect(destructiveActions).toBeVisible();
  await expect(destructiveActions.getByRole('heading', { name: 'Delete book' })).toBeVisible();
  await expect(destructiveActions.getByRole('button', { name: 'Delete book' })).toBeVisible();
  await expect(ordinaryActions.getByRole('button', { name: 'Delete book' })).toHaveCount(0);
});
