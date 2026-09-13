import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow, fetchJsonSafe } from './utils';

/*
 * Book-page actions cleanup contract:
 *   - Four visible controls on the book page (Read now, Favorite, Add to shelf,
 *     and the "More actions" gear); every other action lives in the gear's
 *     accessible menu, with whole-book deletion in an admin-only danger section.
 *   - An "Edit cover" pill on the artwork opens the cover editor, which now
 *     carries the "Library cover" / "My own cover" scope switch that absorbed
 *     the book page's personal-cover controls.
 *   - Per-format downloads + delete/convert/add-format moved from Edit metadata
 *     into a "Files" section at the bottom of the book page.
 *   - Edit metadata keeps no cover controls of its own beyond "Open cover editor".
 *
 * Seed-resilient: specs probe the API for a usable book and skip when absent.
 * Role/feature gating is made deterministic by fetch-then-modify stubs of
 * /api/v1/auth/me; provider fan-out on the cover editor is route-mocked.
 */

/** First seed book that actually carries files, or null. */
async function firstBookWithFormats(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=25', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null))
      .catch(() => null);
    const items: { id: number; formats?: string[] }[] = r?.items ?? [];
    return items.find((b) => (b.formats ?? []).length > 0)?.id ?? null;
  });
}

/** Give the current user every role/feature the book-page actions gate on, in
 *  personal-library mode, plus one pull-delivery device — so the gear menu
 *  renders its complete item set deterministically. */
async function stubFullAccess(page: Page) {
  await page.route('**/api/v1/auth/me', async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: me } = got;
    me.role = { ...me.role, admin: true, edit: true, delete_books: true, download: true, upload: true, viewer: true };
    me.features = { ...me.features, mail_configured: true, hide_books: true, uploading: true };
    me.library_mode = 'personal_library';
    await route.fulfill({ response: res, json: me });
  });
  await page.route('**/api/annotations/devices*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        devices: [{ public_id: 'dev-1', label: 'PocketBook', can_receive_books: true }],
      }),
    });
  });
}

/** The cover editor's provider fan-out never leaves the rig. */
async function mockCoverSources(page: Page) {
  const state = {
    locked: false,
    ereader_enabled: false,
    ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
  };
  await page.route('**/book/*/cover/state', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state) });
  });
  await page.route('**/api/v1/books/*/my-cover', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state) });
  });
  await page.route('**/book/*/cover/candidates*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ candidates: [], providers: [], query: '' }),
    });
  });
}

async function openGearMenu(page: Page) {
  const trigger = page.getByTestId('book-actions-menu');
  await expect(trigger).toBeVisible({ timeout: 10_000 });
  await trigger.click();
  const menu = page.getByTestId('book-actions-menu-list');
  await expect(menu).toBeVisible();
  return menu;
}

test('the gear menu lists every action, with an admin-only delete section', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });

  // The four visible controls — and nothing else action-like in the row.
  const actions = page.getByTestId('book-actions');
  await expect(actions.getByRole('link', { name: 'Read now' })).toBeVisible({ timeout: 10_000 });
  await expect(actions.getByRole('button', { name: /^(Add to favorites|Remove from favorites)$/ })).toBeVisible();
  await expect(actions.getByRole('button', { name: 'Add to shelf' })).toBeVisible();
  await expect(actions.getByTestId('book-actions-menu')).toBeVisible();

  const menu = await openGearMenu(page);
  await expect(menu).toHaveAttribute('role', 'menu');
  const items = menu.getByRole('menuitem');
  await expect(items.filter({ hasText: /Mark as (read|unread)/ })).toHaveCount(1);
  await expect(items.filter({ hasText: /^(Archive|Unarchive)$/ })).toHaveCount(1);
  await expect(items.filter({ hasText: /^(Hide|Unhide)$/ })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Send to e-reader' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Send to device' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Reload metadata from disk' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Remove from library' })).toHaveCount(1);
  await expect(items.filter({ hasText: /^View highlights/ })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Edit metadata' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Edit cover…' })).toHaveCount(1);
  // The destructive section is labelled by who may use it.
  await expect(menu.getByText('Admin only')).toBeVisible();
  await expect(items.filter({ hasText: 'Delete from the global library' })).toHaveCount(1);

  assertNoPageErrors(errors);
});

test('the delete section vanishes without the delete role; the rest of the menu stays', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);
  // One role short of destructive: admin section must not render at all.
  await page.route('**/api/v1/auth/me', async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: me } = got;
    me.role.delete_books = false;
    await route.fulfill({ response: res, json: me });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const menu = await openGearMenu(page);
  await expect(menu.getByRole('menuitem', { name: 'Delete from the global library' })).toHaveCount(0);
  await expect(menu.getByText('Admin only')).toHaveCount(0);
  await expect(menu.getByRole('menuitem', { name: 'Edit metadata' })).toBeVisible();
  await expect(menu.getByRole('menuitem', { name: /Mark as (read|unread)/ })).toBeVisible();
});

test('the menu drives focus by keyboard: open, arrows, Escape restores the trigger', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const trigger = page.getByTestId('book-actions-menu');
  await expect(trigger).toBeVisible({ timeout: 10_000 });

  await trigger.focus();
  await page.keyboard.press('Enter');
  const menu = page.getByTestId('book-actions-menu-list');
  await expect(menu).toBeVisible();
  // Focus lands on the first menuitem and arrows move it.
  await expect(menu.getByRole('menuitem').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('menuitem').nth(1)).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('the Edit cover pill on the artwork opens the cover editor', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await mockCoverSources(page);

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const pill = page.getByTestId('edit-cover-pill');
  await expect(pill).toBeVisible({ timeout: 10_000 });
  // Fine-pointer layouts reveal the pill on hover; coarse layouts always show it.
  await pill.locator('xpath=..').hover();
  await pill.click();
  await expect(page).toHaveURL(new RegExp(`/app/book/${bookId}/cover`), { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: /library cover|own cover/i })).toBeVisible();

  assertNoPageErrors(errors);
});

test('the cover editor scope switch exposes the personal flow', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await mockCoverSources(page);

  await page.goto(`/app/book/${bookId}/cover`, { waitUntil: 'domcontentloaded' });
  const scopeSwitch = page.getByTestId('cover-scope-switch');
  await expect(scopeSwitch).toBeVisible({ timeout: 10_000 });

  await scopeSwitch.getByRole('button', { name: 'My own cover' }).click();
  await expect(page.getByRole('heading', { name: 'Use my own cover' })).toBeVisible();
  await expect(page.getByText(/private to you and your e-reader deliveries/)).toBeVisible();
  expect(page.url()).toContain('personal=1');

  await scopeSwitch.getByRole('button', { name: 'Library cover' }).click();
  await expect(page.getByRole('heading', { name: 'Change library cover' })).toBeVisible();
  expect(page.url()).not.toContain('personal=1');
});

test('a reader without the edit role lands in the personal scope, no switch shown', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await mockCoverSources(page);
  await page.route('**/api/v1/auth/me', async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: me } = got;
    if (me?.role) { me.role.edit = false; me.role.admin = false; }
    await route.fulfill({ response: res, json: me });
  });

  await page.goto(`/app/book/${bookId}/cover`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Use my own cover' })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('cover-scope-switch')).toHaveCount(0);
});

test('the Files section carries downloads, delete, convert and add-a-format', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const files = page.getByTestId('book-files');
  await expect(files).toBeVisible({ timeout: 10_000 });
  await expect(files.getByRole('heading', { name: 'Files' })).toBeVisible();
  await expect(files.locator('a[href*="/download/"]').first()).toBeVisible();
  await expect(files.getByRole('button', { name: /^Delete [A-Z0-9]+/i }).first()).toBeVisible();
  await expect(files.getByLabel('Convert to format')).toBeVisible();
  await expect(files.locator('label', { hasText: 'Add a format' })).toBeVisible();
  // The action row no longer carries per-format download chips.
  await expect(page.getByTestId('book-actions').locator('a[href*="/download/"]')).toHaveCount(0);

  assertNoPageErrors(errors);
});

test('the book page layout holds on a 375px phone: controls wrap, no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('book-actions-menu')).toBeVisible({ timeout: 10_000 });
  await assertNoHorizontalOverflow(page);
  // The gear menu stays inside the viewport when open.
  const menu = await openGearMenu(page);
  const box = (await menu.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(376);
});

test('Edit metadata keeps no inline cover controls; its button opens the cover editor', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await mockCoverSources(page);

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${bookId}/edit`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible({ timeout: 10_000 });

  await expect(page.getByText('Upload image')).toHaveCount(0);
  await expect(page.getByLabel('Cover image URL')).toHaveCount(0);
  await expect(page.getByText(/More cover options/)).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  // Metadata fetch from the web stays.
  await expect(page.getByRole('button', { name: 'Fetch metadata from web' })).toBeVisible();

  await page.getByTestId('open-cover-editor').click();
  await expect(page).toHaveURL(new RegExp(`/app/book/${bookId}/cover\\?origin=edit`), { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: /library cover/i })).toBeVisible();

  assertNoPageErrors(errors);
});
