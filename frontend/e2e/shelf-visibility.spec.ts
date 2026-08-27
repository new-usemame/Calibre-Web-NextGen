import { test, expect, Page } from '@playwright/test';

/*
 * #1734 / #1908 — an ordinary shelf owner was offered "Make public" even
 * though the server requires edit_shelfs for that direction. The decision
 * helper has unit coverage; these route-mocked cases pin its rendered-page
 * wiring so the control cannot drift away from the role and shelf payloads.
 */

const SHELF_ID = 1734;
const SHELF_NAME = 'Visibility fixture';

const json = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

async function mockShelfPage(page: Page, editShelfs: boolean, isPublic: boolean) {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill(json({
    id: 1734,
    name: 'Shelf owner',
    locale: 'en',
    theme: 'dark',
    role: {
      anonymous: false,
      admin: false,
      viewer: true,
      download: true,
      edit_shelfs: editShelfs,
    },
    features: {
      anon_browse: false,
      hide_books: true,
      mail_configured: false,
      public_registration: false,
      kobo_sync: false,
    },
    instance_name: 'Calibre-Web NextGen',
    display: { books_per_page: 24, random_books: 4 },
    catalog: { default_filter: null },
    avatar: null,
  })));

  await page.route(`**/api/v1/shelves/${SHELF_ID}?**`, (route) => route.fulfill(json({
    id: SHELF_ID,
    name: SHELF_NAME,
    is_public: isPublic,
    is_owner: true,
    kobo_sync: false,
    count: 0,
    items: [],
    page: 1,
    per_page: 24,
    total: 0,
    can_edit: true,
  })));
}

async function openMockedShelf(page: Page) {
  await page.goto(`/app/shelf/${SHELF_ID}`);
  await expect(page.getByRole('heading', { level: 1, name: SHELF_NAME })).toBeVisible();
}

test.describe('#1734 shelf visibility control', () => {
  test('does not offer Make public to an owner without edit_shelfs', async ({ page }) => {
    await mockShelfPage(page, false, false);
    await openMockedShelf(page);

    await expect(page.getByRole('button', { name: 'Make public', exact: true })).toHaveCount(0);
  });

  test('offers Make public to an owner with edit_shelfs', async ({ page }) => {
    await mockShelfPage(page, true, false);
    await openMockedShelf(page);

    await expect(page.getByRole('button', { name: 'Make public', exact: true })).toBeVisible();
  });

  test('still offers Make private on an editable public shelf', async ({ page }) => {
    await mockShelfPage(page, true, true);
    await openMockedShelf(page);

    await expect(page.getByRole('button', { name: 'Make private', exact: true })).toBeVisible();
  });
});
