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
  const response = await page.context().request.get(new URL('/api/v1/auth/me', page.url()).href);
  const status = response.status();
  const headers = response.headers();
  const me = await response.json();
  await response.dispose();
  me.role = { ...(me.role ?? {}), delete_books: allowed };

  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({ status, headers, json: me });
  });
}

test('the edit page exposes whole-book deletion to users with delete permission (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}/edit`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toHaveAccessibleName('Delete book');
});

test('the edit page hides whole-book deletion without delete permission (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setDeletePermission(page, false);
  await page.goto(`/app/book/${book.id}/edit`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible();
  await expect(page.getByTestId('edit-book-delete')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete book' })).toHaveCount(0);
});

test('edit-page deletion confirms before mutation and a declined confirm does nothing (#1046)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

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

test('book-detail deletion is grouped outside the ordinary action chips (#1046)', async ({ page, isMobile }) => {
  // Desktop grouping. #1828 deliberately reverses this on mobile, where the
  // heavy region yielded to an icon-level control inside the row — asserted by
  // the mobile counterpart directly below.
  test.skip(isMobile === true, 'desktop grouping — mobile intentionally differs (#1828)');
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  const ordinaryActions = page.getByTestId('book-actions');
  const destructiveActions = page.getByTestId('book-destructive-actions');
  await expect(ordinaryActions).toBeVisible();
  await expect(destructiveActions).toBeVisible();
  // #1939's wording stays on the book-detail region and control. The edit
  // page's separate "Delete book" button remains asserted above.
  await expect(destructiveActions).toHaveAccessibleName('Delete from the global library');
  await expect(destructiveActions.getByRole('heading')).toHaveCount(0);
  const deleteButton = destructiveActions
    .getByRole('button', { name: 'Delete from the global library' });
  await expect(deleteButton).toBeVisible();
  await expect(deleteButton).toHaveText('Delete from the global library');
  await expect(ordinaryActions.getByRole('button', { name: 'Delete from the global library' })).toHaveCount(0);
});

test('mobile groups deletion as an icon-level control inside the action row (#1828)', async ({ page, isMobile }) => {
  test.skip(isMobile !== true, 'mobile-only grouping');
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  // The mobile trade: the destructive control joins the ordinary action row as
  // an icon (the confirm dialog is the guard), and the separated desktop
  // region is not rendered at this width at all. The reporter's ask, verbatim,
  // was that "a red trash can is more than enough for book delete".
  const ordinaryActions = page.getByTestId('book-actions');
  await expect(ordinaryActions).toBeVisible();
  await expect(
    ordinaryActions.getByRole('button', { name: 'Delete from the global library' }),
  ).toHaveCount(1);
  await expect(page.getByTestId('book-destructive-actions')).toHaveCount(0);
});
