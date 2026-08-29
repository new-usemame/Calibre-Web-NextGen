import { test, expect, type Page, type Route } from '@playwright/test';
import type { Book, BooksPage, Me, MetadataUpdate } from '../src/lib/api';

/*
 * #783 — bulk metadata must default to the non-destructive relationship mode,
 * while replace remains available only behind an explicit, count-bearing
 * confirmation. Requests are stubbed so the shared seed library is untouched.
 */

const BOOKS: Book[] = [
  {
    id: 78301, title: 'Bulk metadata first', authors: ['Existing Author'],
    series: null, series_index: null, cover_url: null, formats: ['EPUB'],
    tags: ['Existing tag'], read: false, archived: false,
  },
  {
    id: 78302, title: 'Bulk metadata second', authors: ['Another Author'],
    series: null, series_index: null, cover_url: null, formats: ['EPUB'],
    tags: ['Different tag'], read: false, archived: false,
  },
];

const ADMIN: Me = {
  id: 783,
  name: 'Bulk metadata admin',
  locale: 'en',
  theme: 'dark',
  role: { admin: true, edit: true, edit_shelfs: true, delete_books: true },
};

async function mockCatalog(page: Page) {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(ADMIN),
  }));
  await page.route('**/api/v1/shelves', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }),
  }));
  await page.route('**/api/v1/books?**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/api/v1/books') return route.continue();
    const body: BooksPage = {
      items: BOOKS,
      page: 1,
      per_page: Number(url.searchParams.get('per_page') || 12),
      total: BOOKS.length,
    };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function selectBothAndOpenMetadata(page: Page) {
  await page.goto('/app');
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  // The fixed bulk bar appears after the first selection and can cover a
  // mobile card's centre. Tap the visible upper cover area, as a touch user
  // would, while preserving real hit-testing (no force-click bypass).
  await page.getByRole('button', { name: `Select ${BOOKS[0].title}` })
    .click({ position: { x: 12, y: 12 } });
  await page.getByRole('button', { name: `Select ${BOOKS[1].title}` })
    .click({ position: { x: 12, y: 12 } });
  await page.getByRole('button', { name: 'Edit metadata' }).click();
  await expect(page.getByRole('region', { name: 'Apply metadata' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
  await mockCatalog(page);
});

test('mobile metadata keeps global navigation reachable and stays above the wrapped bulk bar', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await selectBothAndOpenMetadata(page);

  const panel = page.getByRole('region', { name: 'Apply metadata' });
  const bulkBar = page.getByRole('region', { name: '2 selected' });
  const openNavigation = page.getByRole('button', { name: 'Open navigation' });
  await expect(openNavigation).toBeVisible();

  const navigationOwnsItsCenter = await openNavigation.evaluate((button) => {
    const box = button.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return hit === button || (hit !== null && button.contains(hit));
  });
  expect(navigationOwnsItsCenter, 'metadata panel covers the Open navigation hit target').toBe(true);

  const [panelBox, bulkBarBox] = await Promise.all([panel.boundingBox(), bulkBar.boundingBox()]);
  expect(panelBox).not.toBeNull();
  expect(bulkBarBox).not.toBeNull();
  expect(
    panelBox!.y + panelBox!.height,
    'metadata panel overlaps the bulk bar instead of flowing above its actual wrapped height',
  ).toBeLessThanOrEqual(bulkBarBox!.y);
});

for (const viewport of [
  { label: 'narrow and short', width: 375, height: 300 },
  { label: 'landscape phone', width: 667, height: 375 },
]) {
  test(`${viewport.label} keeps every wrapped bulk action reachable and hit-testable`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await selectBothAndOpenMetadata(page);

    const bulkBar = page.getByRole('region', { name: '2 selected' });
    const actionNames = [
      'Mark read',
      'Mark unread',
      'Add to shelf',
      'Edit metadata',
      'Merge',
      'Delete',
      'Clear selection',
    ];

    const actionRows = await bulkBar.getByRole('button').evaluateAll((buttons) =>
      [...new Set(buttons.map((button) => Math.round(button.getBoundingClientRect().top)))],
    );
    expect(actionRows.length, 'the toolbar did not enter the wrapped state').toBeGreaterThan(1);

    for (const name of actionNames) {
      const button = bulkBar.getByRole('button', { name, exact: true });
      await expect(button).toBeVisible();
      await expect(button).toBeInViewport();
      const ownsItsCenter = await button.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return hit === element || (hit !== null && element.contains(hit));
      });
      expect(ownsItsCenter, `${name} does not own its center hit target`).toBe(true);
    }

    page.once('dialog', (dialog) => void dialog.dismiss());
    await bulkBar.getByRole('button', { name: 'Merge', exact: true }).click();
    page.once('dialog', (dialog) => void dialog.dismiss());
    await bulkBar.getByRole('button', { name: 'Delete', exact: true }).click();
  });
}

test('Add is the default, explains the merge, and sends list_mode=add', async ({ page }) => {
  const payloads: MetadataUpdate[] = [];
  await page.route('**/api/v1/books/*/metadata', async (route) => {
    payloads.push(route.request().postDataJSON() as MetadataUpdate);
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await selectBothAndOpenMetadata(page);
  const panel = page.getByRole('region', { name: 'Apply metadata' });
  await expect(panel.getByRole('radio', { name: 'Add to existing' })).toBeChecked();
  await expect(panel).toContainText("will be added after each book's existing values");

  await panel.getByLabel('Tags (comma separated)').fill('Reporter requested tag');
  await panel.getByRole('button', { name: 'Apply to 2 books' }).click();

  await expect.poll(() => payloads.length).toBe(2);
  expect(payloads).toEqual([
    { tags: 'Reporter requested tag', list_mode: 'add' },
    { tags: 'Reporter requested tag', list_mode: 'add' },
  ]);
});

test('Replace names the affected count and dismissal prevents every write', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/v1/books/*/metadata', async (route) => {
    calls += 1;
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await selectBothAndOpenMetadata(page);
  const panel = page.getByRole('region', { name: 'Apply metadata' });
  await panel.getByText('Replace existing', { exact: true }).click();
  await expect(panel.getByRole('radio', { name: 'Replace existing' })).toBeChecked();
  await expect(panel).toContainText("will replace each book's existing values");
  await panel.getByLabel('Tags (comma separated)').fill('Replacement tag');

  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toContain('2 selected book(s)');
    expect(dialog.message()).toContain('will lose their existing values');
    await dialog.dismiss();
  });
  await panel.getByRole('button', { name: 'Apply to 2 books' }).click();
  await page.waitForTimeout(500);
  expect(calls).toBe(0);
});

test('accepting Replace sends list_mode=replace to every selected book', async ({ page }) => {
  const payloads: MetadataUpdate[] = [];
  await page.route('**/api/v1/books/*/metadata', async (route) => {
    payloads.push(route.request().postDataJSON() as MetadataUpdate);
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await selectBothAndOpenMetadata(page);
  const panel = page.getByRole('region', { name: 'Apply metadata' });
  await panel.getByText('Replace existing', { exact: true }).click();
  await expect(panel.getByRole('radio', { name: 'Replace existing' })).toBeChecked();
  await panel.getByLabel('Authors (separate with &)').fill('Replacement Author');
  page.once('dialog', (dialog) => void dialog.accept());
  await panel.getByRole('button', { name: 'Apply to 2 books' }).click();

  await expect.poll(() => payloads.length).toBe(2);
  expect(payloads.every((payload) => payload.list_mode === 'replace')).toBe(true);
  expect(payloads.every((payload) => payload.authors === 'Replacement Author')).toBe(true);
});
