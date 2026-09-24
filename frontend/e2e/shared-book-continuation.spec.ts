import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return { 'X-CSRFToken': (await response.json()).csrf_token };
}

test('a public shelf offers reading and downloads without adding personal membership', async ({ page: admin, secondaryUser }) => {
  const page = secondaryUser.page;
  const headers = await csrf(admin);
  expect((await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
    headers, data: { roles: { browse_global: false }, library_mode: 'personal_library' },
  })).ok()).toBeTruthy();
  const catalog = await (await page.request.get('/api/v1/books?sort=new&per_page=200')).json();
  let book: { id: number; title: string } | undefined;
  for (const candidate of catalog.items) {
    const detail = await (await page.request.get(`/api/v1/books/${candidate.id}`)).json();
    if (detail.formats.some((format: { format: string }) => format.format === 'EPUB')) {
      book = candidate;
      break;
    }
  }
  expect(book, 'fixture needs a readable EPUB').toBeTruthy();
  const selected = book!;
  expect((await page.request.delete(`/api/v1/books/${selected.id}/my-library`, {
    headers: await csrf(page),
  })).ok()).toBeTruthy();
  const created = await admin.request.post('/api/v1/shelves', {
    headers, data: { name: `Shared continuation ${secondaryUser.username}`, is_public: true },
  });
  expect(created.ok()).toBeTruthy();
  const shelf = await created.json();
  try {
    expect((await admin.request.post(`/api/v1/shelves/${shelf.id}/books/${selected.id}`, { headers })).ok()).toBeTruthy();
    await page.goto(`/app/shelf/${shelf.id}`);
    await page.getByRole('link', { name: `Open details for ${selected.title}`, exact: true }).click();
    await expect(page.getByRole('link', { name: 'Read now', exact: true })).toBeVisible();
    // Downloads live in the book page's Files section, one row per format.
    const download = page.getByTestId('book-files').getByRole('listitem')
      .filter({ hasText: 'EPUB' }).locator('a[download]');
    await expect(download).toBeVisible();
    const response = await page.request.get((await download.getAttribute('href'))!);
    expect(response.ok()).toBeTruthy();
    expect((await response.body()).length).toBeGreaterThan(0);
    // Reading through a public shelf never offers membership changes.
    await expect(page.getByTestId('remove-from-my-library')).toHaveCount(0);
    await page.getByTestId('book-actions-menu').click();
    const menu = page.getByTestId('book-actions-menu-list');
    await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Add to library', exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await page.getByRole('link', { name: 'Read now', exact: true }).click();
    await expect(page).toHaveURL(/\/read\//);
    await expect(page.locator('iframe').first()).toBeVisible();
    // Cover pages can be image-only (including SVG wrappers). Exercise an
    // actual page turn and require readable content from the next section.
    await page.getByRole('button', { name: 'Next page', exact: true }).click();
    await expect(page.frameLocator('iframe').first().locator('body')).not.toBeEmpty();
    // The reader's own reading places load for a shared book too (#2284
    // review F4); without membership the panel used to show only an error.
    await page.getByRole('button', { name: 'Reading places', exact: true }).click();
    const places = page.getByRole('dialog', { name: 'Reading places', exact: true });
    await expect(places.getByText('No saved reading places yet.', { exact: true })
      .or(places.getByRole('list'))).toBeVisible();
    await expect(places.getByRole('alert')).toHaveCount(0);
    await places.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(places).toHaveCount(0);
    const detail = await (await page.request.get(`/api/v1/books/${selected.id}`)).json();
    expect(detail.in_my_library).toBe(false);
    expect(detail.accessible_via_public_shelf).toBe(true);
    expect((await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
      headers, data: { roles: { viewer: false, download: false } },
    })).ok()).toBeTruthy();
    await page.goto(`/app/book/${selected.id}`);
    await expect(page.getByRole('heading', { name: selected.title, exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Read now', exact: true })).toHaveCount(0);
    await expect(page.locator('a[download]')).toHaveCount(0);
  } finally {
    expect((await admin.request.post(`/api/v1/shelves/${shelf.id}/delete`, { headers })).ok()).toBeTruthy();
  }
});
