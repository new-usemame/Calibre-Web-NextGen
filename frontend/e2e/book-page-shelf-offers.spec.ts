import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Book pages offer a shelf for adding or removing only where the server allows
 * the change (cps/shelf.py::check_shelf_edit_permissions): a private shelf by
 * its owner only, a public shelf only with "Edit public shelves".
 *
 * Classic: the toolbar's "Remove from shelf" menu listed every visible shelf
 * holding the book, so a reader without the role was offered a public shelf
 * the server then refused, and the menu appeared, empty, when only another
 * reader's private shelf held the book.
 *
 * New UI: the Add to shelf menu and the bulk bar offered every shelf the
 * reader owns, so a public shelf made before an admin took the role away was
 * offered, and the change failed.
 */

async function headersFor(page: Page) {
  const res = await page.request.get('/api/v1/auth/csrf');
  expect(res.ok()).toBeTruthy();
  return { 'X-CSRFToken': ((await res.json()) as { csrf_token: string }).csrf_token };
}

async function createShelf(page: Page, name: string, isPublic: boolean) {
  const res = await page.request.post('/api/v1/shelves', {
    headers: await headersFor(page),
    data: { name, is_public: isPublic },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
  return ((await res.json()) as { id: number }).id;
}

async function shelve(page: Page, shelfId: number, bookId: number) {
  const res = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, {
    headers: await headersFor(page),
  });
  expect(res.ok(), await res.text()).toBeTruthy();
}

async function firstBook(page: Page) {
  const res = await page.request.get('/api/v1/books?per_page=1');
  const books = (await res.json()) as { total: number; items: { id: number; title: string }[] };
  test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');
  return books.items[0];
}

async function deleteShelves(admin: Page, ids: number[]) {
  const headers = await headersFor(admin);
  for (const id of ids) {
    await admin.request.post(`/api/v1/shelves/${id}/delete`, { headers }).catch(() => undefined);
  }
}

test('the classic book page offers removal only from shelves the reader can change', async ({
  page: admin, secondaryUser,
}) => {
  const reader = secondaryUser.page;
  const book = await firstBook(admin);
  const stamp = Date.now();
  const publicShelf = await createShelf(admin, `e2e-offers-public-${stamp}`, true);
  const adminsPrivateShelf = await createShelf(admin, `e2e-offers-admin-${stamp}`, false);
  try {
    await shelve(admin, publicShelf, book.id);
    await shelve(admin, adminsPrivateShelf, book.id);
    const readersName = `e2e-offers-reader-${stamp}`;
    const readersShelf = await createShelf(reader, readersName, false);
    await shelve(reader, readersShelf, book.id);

    await secondaryUser.context.addCookies([
      { name: 'cwng_prefer_spa', value: '0', url: new URL(reader.url()).origin },
    ]);
    const errors = collectPageErrors(reader);
    await reader.goto(`/book/${book.id}`, { waitUntil: 'domcontentloaded' });
    await reader.locator('#removeShelfMenu').click();
    const offers = reader.locator('#remove-from-shelves a[data-shelf-action="remove"]');
    await expect(offers.first()).toBeVisible();
    await expect(offers).toHaveText([readersName]);

    // Once the reader's own shelf lets the book go, only shelves the reader
    // cannot change still hold it: there is nothing to offer.
    const released = await reader.request.post(
      `/api/v1/shelves/${readersShelf}/books/${book.id}/delete`,
      { headers: await headersFor(reader) },
    );
    expect(released.ok(), await released.text()).toBeTruthy();
    await reader.goto(`/book/${book.id}`, { waitUntil: 'domcontentloaded' });
    await expect(reader.locator('#title')).toBeVisible();
    await expect(reader.locator('#removeShelfMenu')).toHaveCount(0);
    assertNoPageErrors(errors);
  } finally {
    await deleteShelves(admin, [publicShelf, adminsPrivateShelf]);
  }
});

test('the new UI offers a public shelf for changes only with "Edit public shelves"', async ({
  page: admin, secondaryUser,
}) => {
  const reader = secondaryUser.page;
  const book = await firstBook(admin);
  const stamp = Date.now();
  const setRole = async (on: boolean) => {
    const res = await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
      headers: await headersFor(admin),
      data: { roles: { edit_shelfs: on } },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
  };
  await setRole(true);
  const publicName = `e2e-offers-made-public-${stamp}`;
  const ownPublicShelf = await createShelf(reader, publicName, true);
  try {
    const privateName = `e2e-offers-own-${stamp}`;
    await createShelf(reader, privateName, false);
    await setRole(false);
    // The premise: the server now refuses the owner that public shelf.
    const refused = await reader.request.post(`/api/v1/shelves/${ownPublicShelf}/books/${book.id}`, {
      headers: await headersFor(reader),
    });
    expect(refused.status()).toBe(403);

    const errors = collectPageErrors(reader);
    await reader.goto(`/app/book/${book.id}`);
    await reader.getByRole('button', { name: 'Add to shelf', exact: true }).click();
    await expect(reader.getByRole('button', { name: privateName })).toBeVisible();
    await expect(reader.getByRole('button', { name: publicName })).toHaveCount(0);

    // The bulk bar offers the same shelves.
    await reader.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
    await reader.goto('/app');
    await reader.getByRole('button', { name: 'Select', exact: true }).click();
    // The fixed bulk bar can cover a mobile card's centre; tap its upper cover
    // area, as a touch user would.
    await reader.getByRole('button', { name: `Select ${book.title}` }).click({ position: { x: 12, y: 12 } });
    const bar = reader.getByRole('region', { name: '1 selected' });
    await bar.getByRole('button', { name: 'Add to shelf', exact: true }).click();
    await expect(bar.getByRole('button', { name: privateName, exact: true })).toBeVisible();
    await expect(bar.getByRole('button', { name: publicName, exact: true })).toHaveCount(0);
    assertNoPageErrors(errors);
  } finally {
    // Without the role its owner cannot delete the public shelf; the admin can.
    // The private one goes with the account.
    await deleteShelves(admin, [ownPublicShelf]);
  }
});
