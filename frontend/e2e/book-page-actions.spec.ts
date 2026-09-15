import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow, fetchJsonSafe } from './utils';

/*
 * Book-page actions cleanup contract:
 *   - A task-ordered visible row on the book page (Read now, Edit cover, Add to
 *     shelf, favorite, personal-library removal, then the "More actions" gear);
 *     every other action lives in the gear's
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

  // The task-ordered visible controls — and nothing else action-like in the row.
  const actions = page.getByTestId('book-actions');
  await expect(actions.getByRole('link', { name: 'Read now' })).toBeVisible({ timeout: 10_000 });
  await expect(actions.getByRole('link', { name: 'Edit cover' })).toBeVisible();
  await expect(actions.getByRole('button', { name: 'Add to shelf' })).toBeVisible();
  await expect(actions.getByRole('button', { name: /^(Add to favorites|Remove from favorites)$/ })).toBeVisible();
  await expect(actions.getByRole('button', { name: 'Remove from my library' })).toBeVisible();
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
  await expect(items.filter({ hasText: 'Remove from library' })).toHaveCount(0);
  await expect(items.filter({ hasText: /^View highlights/ })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Edit metadata' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Edit cover…' })).toHaveCount(1);
  // The destructive section is labelled by who may use it.
  await expect(menu.getByText('Admin only')).toBeVisible();
  await expect(items.filter({ hasText: 'Delete from the global library' })).toHaveCount(1);

  assertNoPageErrors(errors);
});

test('the personal-library action row leads with cover and keeps private removal distinct', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const actions = page.getByTestId('book-actions');
  await expect(actions.getByRole('link', { name: 'Read now' })).toBeVisible();

  // This is the task order, not just a set-membership assertion: the primary
  // reading action is followed by the cover editor and shelf chooser, then two
  // compact personal actions. The spacer leaves Settings at the far edge.
  const visibleActions = await actions.locator('a, button').evaluateAll((nodes) =>
    nodes
      .filter((node) => {
        const style = getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden';
      })
      .map((node) => ({
        name: node.getAttribute('aria-label') || node.textContent?.trim(),
        title: node.getAttribute('title'),
        icon: node.querySelector('svg')?.getAttribute('class') ?? '',
      })),
  );
  expect(visibleActions.slice(0, 6).map((action) => action.name)).toEqual([
    'Read now', 'Edit cover', 'Add to shelf',
    expect.stringMatching(/^(Add to favorites|Remove from favorites)$/),
    'Remove from my library', 'Settings',
  ]);

  const remove = actions.getByRole('button', { name: 'Remove from my library' });
  await expect(remove).toHaveAttribute('title', 'Remove from my library');
  await expect(remove.locator('svg')).toHaveClass(/lucide-book-x/);
  // Do not make a personal membership action look like the global delete path.
  await expect(remove).not.toContainText(/delete/i);
});

test('the delete section is admin-only: a delete-role non-admin never sees it', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);
  // Every destructive role EXCEPT admin: the section must not render at all.
  // (The server-side delete endpoint keeps its own delete+edit check; this is
  // the discoverability layer the operator asked to be admin-only.)
  await page.route('**/api/v1/auth/me', async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: me } = got;
    me.role.admin = false;
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

test('the Edit cover action opens the cover editor', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await mockCoverSources(page);

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const action = page.getByTestId('edit-cover-action');
  await expect(action).toBeVisible({ timeout: 10_000 });
  await action.click();
  await expect(page).toHaveURL(new RegExp(`/app/book/${bookId}/cover`), { timeout: 10_000 });
  await expect(page.getByRole('heading', { name: 'Edit shared library cover' })).toBeVisible();

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

  await scopeSwitch.getByRole('button', { name: 'My private cover' }).click();
  await expect(page.getByRole('heading', { name: 'Edit my private cover' })).toBeVisible();
  await expect(page.getByText(/only your view of this book and copies delivered/)).toBeVisible();
  await expect(page.getByText(/API keys belong to this server/)).toBeVisible();
  expect(page.url()).toContain('personal=1');

  await scopeSwitch.getByRole('button', { name: 'Shared cover' }).click();
  await expect(page.getByRole('heading', { name: 'Edit shared library cover' })).toBeVisible();
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
  await expect(page.getByRole('heading', { name: 'Edit my private cover' })).toBeVisible({ timeout: 10_000 });
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
  const trigger = page.getByTestId('book-actions-menu');
  await expect(trigger).toBeVisible({ timeout: 10_000 });
  // The gear must stay pinned to the TOP-RIGHT of the first row at every
  // narrow width — never wrapping onto a row of its own (which left an empty
  // band under the buttons, item 5 review).
  for (const width of [375, 320]) {
    await page.setViewportSize({ width, height: 667 });
    const [gearBox, readBox] = await Promise.all([
      trigger.boundingBox(),
      page.getByRole('link', { name: 'Read now' }).boundingBox(),
    ]);
    expect(
      Math.abs(gearBox!.y - readBox!.y),
      `gear must share the first row with Read now at ${width}px, not drop to its own row`,
    ).toBeLessThanOrEqual(2);
    await assertNoHorizontalOverflow(page);
  }
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

// ── Behavioural replacements for the deleted Python source pins
// (tests/unit/test_api_v1_edit.py asserted TSX text; these pin the promises
// the pins were named after: the message reaches the user, the note still
// renders, and a metadata-only book has no file controls).

test('a format-delete failure surfaces the message in the Files section', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);
  await page.route(`**/api/v1/books/${bookId}/formats/*/delete`, async (route) => {
    await route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'delete_failed', message: 'Stubbed format failure.' } }),
    });
  });
  page.on('dialog', (d) => void d.accept());

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const files = page.getByTestId('book-files');
  await files.getByRole('button', { name: /^Delete [A-Z0-9]+/i }).first().click();
  await expect(files.getByRole('status')).toContainText('Stubbed format failure.');
});

test('the Files section explains that deleting the last format keeps the book', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('book-files')).toContainText(
    'The book record, metadata, shelves, and reading state stay available.',
    { timeout: 10_000 },
  );
});

test('a metadata-only book shows no file delivery controls', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);
  await page.route(new RegExp(`/api/v1/books/${bookId}(?:\\?.*)?$`), async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: book } = got;
    book.formats = [];
    book.convert_options = { sources: [], targets: [] };
    await route.fulfill({ response: res, json: book });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });
  // No Files section, no download links, no per-format delete buttons.
  await expect(page.getByTestId('book-files')).toHaveCount(0);
  await expect(page.locator('a[href*="/download/"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Delete [A-Z0-9]+/i })).toHaveCount(0);
  // The send routes gate on having files, so neither appears in the menu.
  const menu = await openGearMenu(page);
  await expect(menu.getByRole('menuitem', { name: 'Send to e-reader' })).toHaveCount(0);
  await expect(menu.getByRole('menuitem', { name: 'Send to device' })).toHaveCount(0);
});

test('a whole-book delete shows the API warning after it succeeds', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await page.route(`**/api/v1/books/${bookId}/delete`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ warning: { message: 'Stubbed delete warning.' } }),
    });
  });
  const dialogs: string[] = [];
  page.on('dialog', (d) => { dialogs.push(`${d.type()}:${d.message()}`); void d.accept(); });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const menu = await openGearMenu(page);
  await menu.getByRole('menuitem', { name: 'Delete from the global library' }).click();
  await expect.poll(() => dialogs.some((d) => d === 'alert:Stubbed delete warning.')).toBe(true);
});

test('the open gear menu stays inside the viewport even when the row overflows (CI font metrics)', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('book-actions-menu')).toBeVisible({ timeout: 10_000 });
  // CI's cold contexts put the gear's anchor past the right edge AND changed
  // the panel's content size after the one-shot clamp (PR #2237 run: right
  // edge 383.8–386.2px at a 375px viewport). Reproduce both halves: park the
  // trigger's wrapper 24px past the viewport's right edge…
  await page.addStyleTag({
    content: 'div:has(> [data-testid="book-actions-menu"]) { position: fixed; right: -24px; bottom: 140px; z-index: 999; }',
  });
  const menu = await openGearMenu(page);
  // …then grow the open panel — the clamp must re-run, not hold the stale
  // measurement. (The bundle ships no webfonts to delay; a late item-style
  // change is the same resize signal the ResizeObserver must catch.)
  await page.addStyleTag({
    content: '[role="menu"] [role="menuitem"] { font-size: 18px; }',
  });
  await expect
    .poll(async () => {
      const box = await menu.boundingBox();
      return box ? box.x + box.width : -1;
    }, { message: 'the menu must re-clamp inside the viewport after it resizes' })
    .toBeLessThanOrEqual(376);
  const box = (await menu.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
});

test('Files download links carry the app mount prefix', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  await stubFullAccess(page);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const link = page.getByTestId('book-files').locator('a[href*="/download/"]').first();
  await expect(link).toBeVisible({ timeout: 10_000 });
  // The prefix is whatever the shell injected; the rendered href must carry it
  // (resourceUrl at consumption), or the link 404s behind a reverse-proxy
  // subpath (#571). Compares against the app's own runtime value, not a
  // hardcoded root.
  const prefix = await page.evaluate(
    () => (window as unknown as { __CWNG_PREFIX__?: string }).__CWNG_PREFIX__ ?? '',
  );
  const href = await link.getAttribute('href');
  expect(href!.startsWith(`${prefix}/download/${bookId}/`)).toBe(true);
});

test('a read book shows a visible Read ✓ state badge; unread shows none', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  test.skip(bookId == null, 'seed has no book with files');
  let read = true;
  await page.route(new RegExp(`/api/v1/books/${bookId}(?:\\?.*)?$`), async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: book } = got;
    book.read = read;
    await route.fulfill({ response: res, json: book });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  // The badge text is the plain 'Read' msgid + ✓ (Dutch renders "Gelezen ✓" —
  // the status word must never be an untranslated composite).
  await expect(page.getByTestId('book-read-badge')).toHaveText('Read ✓', { timeout: 10_000 });

  read = false;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('book-actions-menu')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('book-read-badge')).toHaveCount(0);
});

/* Description clamp: long descriptions show ~5 lines with a fade and a
 * Show more/Show less button; short ones never show the control. The
 * description HTML is stubbed so the seed's own copy doesn't matter. */

const LONG_DESCRIPTION = Array.from({ length: 12 }, (_, i) =>
  `<p>Paragraph ${i + 1} of the sentinel long description, padded with enough plain words to overflow a five-line clamp on any viewport width.</p>`,
).join('');

async function stubDescriptionHtml(page: Page, html: string) {
  await page.goto('/app');
  const bookId = await firstBookWithFormats(page);
  if (bookId == null) return null;
  await page.route(new RegExp(`/api/v1/books/${bookId}(?:\\?.*)?$`), async (route) => {
    const got = await fetchJsonSafe(route);
    if (!got) return;
    const { response: res, body: book } = got;
    book.description_html = html;
    await route.fulfill({ response: res, json: book });
  });
  return bookId;
}

test('a long description clamps to five lines with Show more / Show less', async ({ page }) => {
  const bookId = await stubDescriptionHtml(page, LONG_DESCRIPTION);
  test.skip(bookId == null, 'seed has no book with files');

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const desc = page.getByTestId('book-description');
  await expect(desc).toBeVisible({ timeout: 10_000 });
  // Bind by testid, not by name: the accessible name flips with the state.
  const toggle = page.getByTestId('description-toggle');
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveText('Show more');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  // line-clamp truncates the box itself, so overflow can't be probed via
  // scrollHeight — the clamp's presence is the observable contract.
  await expect(desc).toHaveCSS('-webkit-line-clamp', '5');

  await toggle.click();
  await expect(toggle).toHaveText('Show less');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(desc).toHaveCSS('-webkit-line-clamp', 'none');

  await toggle.click();
  await expect(toggle).toHaveText('Show more');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
});

test('a short description never shows the clamp control', async ({ page }) => {
  const bookId = await stubDescriptionHtml(page, '<p>One short sentinel line.</p>');
  test.skip(bookId == null, 'seed has no book with files');

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('book-description')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole('button', { name: /Show (more|less)/ })).toHaveCount(0);
});
