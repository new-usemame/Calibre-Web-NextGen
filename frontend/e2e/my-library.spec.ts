// Change this import to './fixtures' when ci/1927 reaches main; its
// secondaryUser contract is identical.
import { test, expect, type SecondaryUserSession } from './my-library-fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

interface MePayload {
  id: number;
  library_mode: 'monolibrary' | 'personal_library';
  role: Record<string, boolean>;
}

interface GlobalBook {
  id: number;
  title: string;
  in_my_library: boolean;
}

async function csrfHeaders(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  const payload = (await response.json()) as { csrf_token: string };
  return { 'X-CSRFToken': payload.csrf_token };
}

async function setManagedMode(
  adminPage: Page,
  userId: number,
  libraryMode: MePayload['library_mode'],
  browseGlobal = true,
) {
  const response = await adminPage.request.post(`/api/v1/admin/users/${userId}`, {
    headers: await csrfHeaders(adminPage),
    data: { roles: { browse_global: browseGlobal }, library_mode: libraryMode },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function removeMembership(page: Page, bookId: number) {
  const response = await page.request.delete(`/api/v1/books/${bookId}/my-library`, {
    headers: await csrfHeaders(page),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function addMembership(page: Page, bookId: number) {
  const response = await page.request.put(`/api/v1/books/${bookId}/my-library`, {
    headers: await csrfHeaders(page),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function firstGlobalBooks(page: Page): Promise<GlobalBook[]> {
  const response = await page.request.get('/api/v1/library/global?sort=new&per_page=10');
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { items: GlobalBook[] }).items;
}

async function expectNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const failures = results.violations
    .filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
    .map((violation) => `${violation.id}: ${violation.help}`);
  expect(failures).toEqual([]);
}

test.describe('My Library', () => {
  test('two real accounts see independent selections and can discover missing books', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    const adminMe = (await adminPage.request.get('/api/v1/auth/me').then((r) => r.json())) as MePayload;
    const originalMode = adminMe.library_mode;
    const originalBrowseGlobal = !!adminMe.role.browse_global;
    const secondaryPage = secondaryUser.page;
    let books: GlobalBook[] = [];

    try {
      await setManagedMode(adminPage, adminMe.id, 'personal_library');
      await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
      books = await firstGlobalBooks(adminPage);
      test.skip(books.length < 2, 'seed library needs at least two books');
      const [adminBook, secondaryBook] = books;

      // Seed-once initially gives both accounts every visible book. Curate each
      // in the opposite direction so the same global records prove isolation.
      await removeMembership(adminPage, secondaryBook.id);
      await removeMembership(secondaryPage, adminBook.id);

      const adminIds = ((await adminPage.request.get('/api/v1/books?per_page=200').then((r) => r.json())) as {
        items: Array<{ id: number }>;
      }).items.map((book) => book.id);
      const secondaryIds = ((await secondaryPage.request.get('/api/v1/books?per_page=200').then((r) => r.json())) as {
        items: Array<{ id: number }>;
      }).items.map((book) => book.id);
      expect(adminIds).toContain(adminBook.id);
      expect(adminIds).not.toContain(secondaryBook.id);
      expect(secondaryIds).toContain(secondaryBook.id);
      expect(secondaryIds).not.toContain(adminBook.id);

      await adminPage.goto('/app');
      const adminGrid = adminPage.getByTestId('catalog-grid');
      await expect(adminGrid.getByRole('link', { name: `Open details for ${adminBook.title}` })).toBeVisible();
      await expect(adminGrid.getByRole('link', { name: `Open details for ${secondaryBook.title}` })).toHaveCount(0);

      await secondaryPage.goto('/app');
      const secondaryGrid = secondaryPage.getByTestId('catalog-grid');
      await expect(secondaryGrid.getByRole('link', { name: `Open details for ${secondaryBook.title}` })).toBeVisible();
      await expect(secondaryGrid.getByRole('link', { name: `Open details for ${adminBook.title}` })).toHaveCount(0);

      await expect(adminPage.getByRole('link', { name: 'Global Library', includeHidden: true }))
        .toHaveAttribute('href', '/app/global');
      await adminPage.goto('/app/global');
      await expect(adminPage).toHaveURL(/\/app\/global/);
      await expect(adminPage.getByRole('heading', { name: 'Global Library' })).toBeVisible();
      await adminPage.getByRole('button', { name: 'Not in your library' }).click();
      await expect(adminPage.getByRole('button', { name: `Add ${secondaryBook.title} to my library` })).toBeVisible();
      await expect(adminPage.getByRole('button', { name: `Add ${adminBook.title} to my library` })).toHaveCount(0);
      await expectNoSeriousAxeViolations(adminPage);

      await adminPage.getByRole('button', { name: `Add ${secondaryBook.title} to my library` }).click();
      await expect(adminPage.getByText('Added to your library', { exact: true })).toBeAttached();
      await adminPage.goto('/app');
      await expect(adminPage.getByTestId('catalog-grid').getByRole('link', {
        name: `Open details for ${secondaryBook.title}`,
      })).toBeVisible();

      await secondaryPage.goto('/app/account');
      await expect(secondaryPage.getByRole('radio', { name: /My selection/ })).toBeChecked();
      await expectNoSeriousAxeViolations(secondaryPage);
      const wholeLibraryConfirm = secondaryPage.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain('Your selection is kept exactly as you left it');
        await dialog.accept();
      });
      await Promise.all([
        wholeLibraryConfirm,
        secondaryPage.getByRole('radio', { name: /The whole library/ }).click(),
      ]);
      await expect(secondaryPage.getByRole('radio', { name: /The whole library/ })).toBeChecked();
      const selectionConfirm = secondaryPage.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain('Your library goes back to the books you had chosen');
        await dialog.accept();
      });
      await Promise.all([
        selectionConfirm,
        secondaryPage.getByRole('radio', { name: /My selection/ }).click(),
      ]);
      await secondaryPage.goto('/app');
      await expect(secondaryPage.getByTestId('catalog-grid').getByRole('link', {
        name: `Open details for ${adminBook.title}`,
      })).toHaveCount(0);
    } finally {
      if (books[0]) await addMembership(secondaryPage, books[0].id).catch(() => undefined);
      if (books[1]) await addMembership(adminPage, books[1].id).catch(() => undefined);
      await setManagedMode(adminPage, adminMe.id, originalMode, originalBrowseGlobal);
    }
  });

  test('intro dismissal is server-side and survives a fresh browser page', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    await page.goto('/app');
    const introduction = page.getByText('New: your own library');
    await expect(introduction).toBeVisible();
    await page.getByRole('button', { name: 'Dismiss library introduction' }).click();
    await expect(introduction).toHaveCount(0);
    const freshPage = await secondaryUser.context.newPage();
    await freshPage.goto('/app');
    await expect(freshPage.getByText('New: your own library')).toHaveCount(0);
    await freshPage.close();
  });

  test('classic theme exposes the same library, global, add, and remove surfaces', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    test.skip(test.info().project.name === 'mobile', 'classic parity is exercised once on desktop');
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const books = await firstGlobalBooks(page);
    test.skip(books.length < 2, 'seed library needs at least two books');
    const [missingBook, ownedBook] = books;
    await removeMembership(page, missingBook.id);

    try {
      await page.goto('/?cwng_feedback=newui');
      await expect(page.locator('#my-library-intro')).toContainText('New: your own library');
      await expect(page.getByRole('link', { name: 'Global Library' })).toHaveAttribute('href', '/global-library');

      await page.goto(`/book/${ownedBook.id}`);
      const removeButton = page.locator('#remove-from-my-library-btn');
      await expect(removeButton).toHaveAttribute('aria-label', 'Remove from my library');
      await removeButton.click();
      const removeDialog = page.locator('#removeFromMyLibraryModal');
      await expect(removeDialog).toContainText(`Remove "${ownedBook.title}" from your library?`);
      await expect(removeDialog).toContainText('Nothing is deleted: the book stays in the global library');
      await removeDialog.getByRole('button', { name: 'Cancel' }).click();

      await page.goto('/global-library/recent-missing');
      await expect(page.getByRole('heading', { name: /Global Library/ })).toBeVisible();
      const addButton = page.getByRole('button', { name: `Add ${missingBook.title} to my library` });
      await expect(addButton).toBeVisible();
      await addButton.click();
      await expect(addButton).toHaveCount(0);
    } finally {
      await addMembership(page, missingBook.id).catch(() => undefined);
    }
  });
});
