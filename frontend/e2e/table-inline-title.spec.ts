import { test, expect } from './fixtures';
import type { APIRequestContext, Locator, Page } from '@playwright/test';

/*
 * Fork #783 — the table view got a safe inline title edit: a pencil beside each
 * title, gated on the account's edit permission, saving through the same
 * metadata mutation the edit page uses.
 *
 * This spec REPLACES the source-text pins that used to stand in for it in
 * tests/unit/test_783_table_inline_title.py, which asserted that Table.tsx
 * literally contained `useMe().data?.role?.edit`, `useUpdateMetadata(book.id)`,
 * `update.mutate({ title }`, `event.key === 'Enter'`, `event.key === 'Escape'`,
 * `Edit title for {title}`, `Cancel title edit` and `role="alert"`.
 *
 * On PR #2115 the contributor bound `const me = useMe().data` and derived
 * `const canEdit = !!me?.role?.edit` — the permission gate and the mutation were
 * untouched, only the spelling changed, and the pin reported the feature gone.
 * `~/.claude/TESTING-STRATEGY.md` forbids source-text pins; what those lines
 * were trying to say is written below as things a user can and cannot do.
 *
 * The renamed book is chosen to avoid the books other specs own, and is
 * restored through the API in a finally.
 */

/** Book fixtures other specs identify by title; renaming one makes them skip. */
const SPOKEN_FOR_TITLES = new Set(['RTL Vertical Sample', 'LTR Horizontal Sample']);
/**
 * The rename is the original title plus this marker, rather than a fresh
 * string, so a run killed between the save and the restore leaves something
 * any later run can undo. `pickEditableBook` repairs those before choosing.
 */
const PROBE_SUFFIX = ' [#783 inline-edit probe]';
/** The table's first page (useBooks' default per_page), so no scrolling. */
const FIRST_PAGE = 24;

interface BookItem {
  id: number;
  title: string;
}

async function csrfToken(api: APIRequestContext): Promise<string> {
  const res = await api.get('/api/v1/auth/csrf');
  expect(res.ok(), `CSRF fetch failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function serverTitle(api: APIRequestContext, id: number): Promise<string> {
  const res = await api.get(`/api/v1/books/${id}/metadata`);
  expect(res.ok(), `metadata read for ${id} failed: ${res.status()}`).toBeTruthy();
  return String(((await res.json()) as { title?: unknown }).title ?? '');
}

async function setServerTitle(api: APIRequestContext, id: number, title: string) {
  const res = await api.post(`/api/v1/books/${id}/metadata`, {
    headers: { 'X-CSRFToken': await csrfToken(api) },
    data: { title },
  });
  expect(res.ok(), `metadata write for ${id} failed: ${res.status()}`).toBeTruthy();
}

/**
 * A book on the table's first page that no other spec has claimed.
 *
 * rtl-auto-direction and edit-rating rewrite `per_page=1`, i.e. the newest
 * book; series-sort-order.spec.ts owns the three at the far end of the same
 * listing; reader-rtl finds its two fixtures by exact title. Taking any of
 * those would turn this spec's mutation into someone else's flake.
 */
async function pickEditableBook(api: APIRequestContext): Promise<BookItem> {
  const res = await api.get('/api/v1/books?per_page=250');
  expect(res.ok(), `book listing failed: ${res.status()}`).toBeTruthy();
  const items = ((await res.json()) as { items: BookItem[] }).items ?? [];

  // Undo a rename a killed run could not. Left alone it would become the
  // "original" title the next restore writes back, i.e. permanent.
  for (const b of items) {
    if (!b.title.endsWith(PROBE_SUFFIX)) continue;
    b.title = b.title.slice(0, -PROBE_SUFFIX.length);
    await setServerTitle(api, b.id, b.title);
  }

  const reserved = new Set([items[0]?.id, ...items.slice(-3).map((b) => b.id)]);
  // The edit control is named after its book, so a library holding the same
  // title twice (a real local-dev seed does) makes that name ambiguous rather
  // than wrong. Require a title this listing holds exactly once.
  const titleCounts = new Map<string, number>();
  for (const b of items) titleCounts.set(b.title, (titleCounts.get(b.title) ?? 0) + 1);
  const book = items
    .slice(0, FIRST_PAGE)
    .find((b) => !reserved.has(b.id)
      && !SPOKEN_FOR_TITLES.has(b.title)
      && titleCounts.get(b.title) === 1);
  expect(
    book,
    `no unclaimed, uniquely-titled book on the table's first page (${items.length} in the library)`,
  ).toBeTruthy();
  return book!;
}

/**
 * The table row for one book, identified by its own detail link.
 *
 * Only valid while that cell is NOT being edited: the edit form replaces the
 * detail link, so an editing row stops matching. Reach the open form through
 * `titleInput` instead — at most one cell is editable at a time.
 */
function rowFor(page: Page, id: number): Locator {
  return page.getByRole('row').filter({ has: page.locator(`a[href$="/book/${id}"]`) });
}

/** The open inline-title editor.
 *
 *  Exact, and by role: `getByLabel('Title')` substring-matches, so it also
 *  collects every "Edit title for …" pencil in the table. */
function titleInput(page: Page): Locator {
  return page.getByRole('textbox', { name: 'Title', exact: true });
}

// Serial: every test here picks and mutates the same book. In parallel they
// race on each other's rename rather than on the product.
test.describe.configure({ mode: 'serial' });

test.describe('#783 inline title editing in the table view', () => {
  test('an editor renames a book from the table and the rename persists', async ({ page }) => {
    const book = await pickEditableBook(page.request);
    const newTitle = `${book.title}${PROBE_SUFFIX}`;

    try {
      await page.goto('/app/table');
      const row = rowFor(page, book.id);
      await expect(row).toBeVisible();

      // The affordance is named after the book it edits — that name is how a
      // screen-reader user tells twenty identical pencils apart.
      // `exact` throughout: accessible-name matching is substring-based by
      // default, so "Edit title for Metamorphosis" would also match the row
      // still carrying the probe suffix — the exact confusion to avoid here.
      const edit = row.getByRole('button', { name: `Edit title for ${book.title}`, exact: true });
      await expect(edit).toBeVisible();
      await edit.click();

      const input = titleInput(page);
      await expect(input).toBeFocused();
      await input.fill(newTitle);

      // Enter saves — no pointer required. Bounded well inside the test
      // timeout and resolved to null on expiry, so a save that never reaches
      // the server reports THAT rather than stranding the restore below.
      const saved = page
        .waitForResponse(
          (r) => r.url().includes(`/api/v1/books/${book.id}/metadata`)
            && r.request().method() === 'POST'
            && r.status() === 200,
          { timeout: 10_000 },
        )
        .catch(() => null);
      await input.press('Enter');
      expect(
        await saved,
        'the inline save did not POST the canonical metadata mutation',
      ).not.toBeNull();

      await expect(row.getByRole('link', { name: newTitle, exact: true })).toBeVisible();

      // Persisted, not merely repainted: the server is the one that has to
      // agree, and the row has to still say so after a full reload.
      expect(await serverTitle(page.request, book.id)).toBe(newTitle);
      await page.reload();
      await expect(rowFor(page, book.id).getByRole('link', { name: newTitle, exact: true })).toBeVisible();
      // …and the edit control follows the new title.
      await expect(
        rowFor(page, book.id).getByRole('button', { name: `Edit title for ${newTitle}`, exact: true }),
      ).toBeVisible();
    } finally {
      await setServerTitle(page.request, book.id, book.title);
    }
  });

  test('Escape abandons an edit without writing anything', async ({ page }) => {
    const book = await pickEditableBook(page.request);
    let writes = 0;
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.url().includes(`/api/v1/books/${book.id}/metadata`)) writes += 1;
    });

    await page.goto('/app/table');
    const row = rowFor(page, book.id);
    await row.getByRole('button', { name: `Edit title for ${book.title}`, exact: true }).click();

    const input = titleInput(page);
    await input.fill('#783 abandoned edit');
    await input.press('Escape');

    // Back to a plain title cell, with the original title, and nothing sent.
    await expect(row.getByRole('link', { name: book.title, exact: true })).toBeVisible();
    await expect(input).toHaveCount(0);
    expect(writes, 'Escape must not save').toBe(0);
    expect(await serverTitle(page.request, book.id)).toBe(book.title);

    // The explicit cancel control is equivalent, and is reachable by name.
    await row.getByRole('button', { name: `Edit title for ${book.title}`, exact: true }).click();
    await titleInput(page).fill('#783 abandoned again');
    await page.getByRole('button', { name: 'Cancel title edit' }).click();
    await expect(row.getByRole('link', { name: book.title, exact: true })).toBeVisible();
    expect(writes, 'cancel must not save').toBe(0);
  });

  test('a rejected save is announced and does not silently drop the edit', async ({ page }) => {
    const book = await pickEditableBook(page.request);
    let rejected = 0;
    await page.route(`**/api/v1/books/${book.id}/metadata`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      rejected += 1;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'server_error', message: 'nope' } }),
      });
    });

    await page.goto('/app/table');
    const row = rowFor(page, book.id);
    await row.getByRole('button', { name: `Edit title for ${book.title}`, exact: true }).click();
    await titleInput(page).fill('#783 rejected rename');
    await titleInput(page).press('Enter');

    // Instrument check FIRST. If this route ever stopped matching, the save
    // would succeed, the cell would repaint, and the assertions below would
    // still find something to look at — a green that measured nothing, and a
    // real rename left in the library. Prove the failure was delivered.
    await expect
      .poll(() => rejected, { message: 'the simulated save failure never reached the page' })
      .toBe(1);

    // An assertive live region IN THE CELL, so the failure reaches a user who
    // is not watching it. Without one the edit just appears to do nothing.
    // Scoped: the app also mounts a global sr-only role=alert announcer, and a
    // page-wide lookup would match that empty region as readily as this one.
    const editedCell = page.locator('td').filter({ has: titleInput(page) });
    const announcement = editedCell.getByRole('alert');
    await expect(announcement, 'the rejected save is not announced in the cell').toBeVisible();
    await expect(announcement, 'the announcement says nothing').not.toBeEmpty();
    // The draft is still there to retry or cancel — not discarded.
    await expect(titleInput(page)).toHaveValue('#783 rejected rename');
    // Nothing was written, so nothing needs restoring: the route above never
    // let the request reach the server, and `rejected` proves it fired.
  });

  test('an account without edit permission gets no inline edit control', async ({
    page,
    secondaryUser,
  }) => {
    const book = await pickEditableBook(page.request);
    const viewer = secondaryUser.page;

    const me = await viewer.request.get('/api/v1/auth/me').then((r) => r.json()) as {
      role: { edit: boolean };
    };
    expect(me.role.edit, 'this fixture account is supposed to lack edit').toBe(false);

    await viewer.goto('/app/table');
    // It can read the table — the point is the missing control, not a blocked page.
    await expect(rowFor(viewer, book.id).getByRole('link', { name: book.title, exact: true })).toBeVisible();
    await expect(
      viewer.getByRole('button', { name: /^Edit title for / }),
      'a viewer must not be offered an inline title edit',
    ).toHaveCount(0);

    // And the editor session, against the same rows, is offered one — otherwise
    // "no button" would also pass on a table that renders no buttons at all.
    await page.goto('/app/table');
    await expect(
      rowFor(page, book.id).getByRole('button', { name: `Edit title for ${book.title}`, exact: true }),
    ).toBeVisible();
  });
});
