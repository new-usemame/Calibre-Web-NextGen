import { test, expect, type Page } from '@playwright/test';
import type { Book, BooksPage, BookDetail, BookMetadata } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Fork issue #1169 — "items disappear from results after edit", reported by
 * @magdalar on v4.1.21: edit a book, return to the library, and the book you
 * just edited is gone from the grid until the page is reloaded.
 *
 * Root cause is delete semantics applied to an edit. useUpdateMetadata's
 * onSuccess called removeBookFromCache(id), the helper written for a real
 * DELETE (#578: drop the book from every cached catalog snapshot so a later
 * scroll-restore can't resurrect a ghost card that 404s on click). An edited
 * book still exists and still belongs in the listing, so evicting it from the
 * restored accumulator leaves the refetch to re-introduce it — and dedupAppend
 * only ever UPSERTS or APPENDS. A book that was first in a Newest-sorted
 * library comes back as the LAST card of everything loaded so far: measured at
 * index 0 before the edit and index 23 of 24 after it. From the top of the
 * grid, where the reporter was looking, that is indistinguishable from gone,
 * and it stays that way until a reload drops the in-memory snapshot cache.
 *
 * The spec drives the reporter's flow against a mocked two-page library so the
 * "edited book lives on a page we are no longer sitting on" condition is
 * deterministic rather than a function of the seed size. Navigation after the
 * first load is client-side throughout: a full page.goto() would drop the
 * module-level snapshot cache and take the bug with it.
 */

const TARGET_ID = 9001;
const OLD_TITLE = 'Mock book awaiting an edit';
const NEW_TITLE = 'Mock book after the edit';

function fakeBook(id: number, title: string): Book {
  return {
    id,
    title,
    authors: ['Mock Author'],
    series: null,
    series_index: null,
    cover_url: null,
    formats: ['EPUB'],
    tags: [],
    read: false,
    archived: false,
  };
}

function metadata(title: string): BookMetadata {
  return {
    id: TARGET_ID,
    title,
    authors: 'Mock Author',
    series: '',
    series_index: '',
    tags: '',
    publishers: '',
    languages: '',
    comments: '',
    rating: 0,
    pubdate: '',
    identifiers: [],
  };
}

function detail(title: string): BookDetail {
  return {
    id: TARGET_ID,
    title,
    authors: [{ id: 1, name: 'Mock Author' }],
    series: null,
    series_index: '',
    rating: null,
    cover_url: null,
    pubdate: null,
    date_added: null,
    last_modified: null,
    description_html: null,
    original_filename: null,
    tags: [],
    languages: [],
    publishers: [],
    identifiers: [],
    formats: [],
    read: false,
    archived: false,
    favorited: false,
    hidden: false,
  } as BookDetail;
}

/** Two pages of library, with the book under test first on page 1. The grid
 *  asks for rowsPerLoad × measured columns, so per_page is read off the request
 *  rather than dictated (#1130/#1144). */
async function mockLibrary(page: Page, currentTitle: () => string) {
  await page.route('**/api/v1/books?**', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const url = new URL(route.request().url());
    const pageNumber = Number(url.searchParams.get('page'));
    if (url.pathname !== '/api/v1/books' || (pageNumber !== 1 && pageNumber !== 2)) {
      return route.continue();
    }
    const perPage = Number(url.searchParams.get('per_page'));
    const firstId = (pageNumber - 1) * perPage + 1;
    const items = Array.from({ length: perPage }, (_, i) => {
      const n = firstId + i;
      return n === 1 ? fakeBook(TARGET_ID, currentTitle()) : fakeBook(10_000 + n, `Filler ${n}`);
    });
    const body: BooksPage = { items, page: pageNumber, per_page: perPage, total: perPage * 2 };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

/** The edit form's own endpoints. The POST is what the fix hangs off, so it
 *  returns the renamed metadata the way the server would. */
async function mockEditEndpoints(page: Page, setTitle: (t: string) => void, currentTitle: () => string) {
  await page.route(`**/api/v1/books/${TARGET_ID}/metadata`, async (route) => {
    if (route.request().method() === 'POST') {
      const sent = route.request().postDataJSON() as Partial<BookMetadata>;
      if (typeof sent.title === 'string' && sent.title) setTitle(sent.title);
      return route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify(metadata(currentTitle())),
      });
    }
    await route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(metadata(currentTitle())),
    });
  });
  await page.route(`**/api/v1/books/${TARGET_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(detail(currentTitle())),
    });
  });
}

function gridBookLinks(page: Page) {
  return page.locator('main a[href*="/book/"]:not([href$="/edit"])');
}

function targetCard(page: Page) {
  return page.locator(`main a[href$="/book/${TARGET_ID}"]`);
}

/** Where the book under test sits among the loaded cards — the thing the
 *  reporter actually notices. -1 when it isn't rendered at all. */
async function targetIndex(page: Page): Promise<number> {
  return await gridBookLinks(page).evaluateAll(
    (links, id) => links.findIndex(
      (l) => (l as HTMLAnchorElement).getAttribute('href')!.endsWith(`/book/${id}`)),
    TARGET_ID);
}

test.describe('#1169 an edited book stays in the library listing', () => {
  test.beforeEach(async ({ page }) => {
    // Keep the optional Discover strip's links out of the grid counts.
    await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
    // Deterministic paging: drive page 2 through the explicit Load more button
    // instead of racing the sentinel's observer.
    await page.addInitScript(() => {
      class NeverIntersectingObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
        takeRecords() { return []; }
      }
      window.IntersectionObserver = NeverIntersectingObserver as unknown as typeof IntersectionObserver;
    });
  });

  test('the book is still listed, with its new title, after edit → back', async ({ page }) => {
    let title = OLD_TITLE;
    await mockLibrary(page, () => title);
    await mockEditEndpoints(page, (t) => { title = t; }, () => title);

    const errors = collectPageErrors(page);
    await page.goto('/app');

    // Load a second page, so the edited book (page 1) is not on the page the
    // catalog will re-request on remount. This is the reporter's condition: a
    // library big enough to scroll before editing something near the top.
    const loadMore = page.getByRole('button', { name: 'Load more' });
    await expect(loadMore).toBeVisible();
    const firstPageCount = await gridBookLinks(page).count();
    await loadMore.click();
    await expect(gridBookLinks(page)).toHaveCount(firstPageCount * 2);
    await expect(targetCard(page)).toHaveCount(1);
    const indexBefore = await targetIndex(page);
    expect(indexBefore, 'the book under test starts at the top of the grid').toBe(0);

    // Client-side into the edit form via the card's own quick-edit control
    // (hover-revealed on desktop, always shown for touch).
    const card = targetCard(page).first();
    await card.hover();
    const quickEdit = page.locator(`main a[href$="/book/${TARGET_ID}/edit"]`).first();
    await quickEdit.click();
    await page.waitForURL(`**/book/${TARGET_ID}/edit`);

    const titleField = page.getByLabel(/^title$/i).first();
    await expect(titleField).toHaveValue(OLD_TITLE);
    await titleField.fill(NEW_TITLE);
    await page.getByRole('button', { name: /save changes/i }).click();
    await page.waitForURL(`**/book/${TARGET_ID}`, { timeout: 15_000 });

    // Back to the library the way the reporter does — a client-side return, not
    // a reload. history: /app → /book/N/edit → /book/N.
    await page.goBack();
    await page.waitForURL(`**/book/${TARGET_ID}/edit`);
    await page.goBack();
    await page.waitForURL((u) => !/\/book\//.test(u.pathname));

    // The whole listing is still there and the book is still in it…
    await expect(gridBookLinks(page)).toHaveCount(firstPageCount * 2);
    await expect(targetCard(page), 'the edited book left the library listing (#1169)').toHaveCount(1);
    // …carrying the edit rather than a stale pre-edit card…
    await expect(targetCard(page).first()).toContainText(NEW_TITLE);
    // …and still where the user left it. Pre-fix the eviction sent it to the
    // end of everything loaded (index 0 → 23 of 24): present in the DOM, gone
    // from where anyone would look for it.
    await expect
      .poll(() => targetIndex(page), {
        message: 'the edited book was moved to the end of the library listing (#1169)',
      })
      .toBe(indexBefore);

    assertNoPageErrors(errors);
  });

  /*
   * The other half of the same decision, raised by the cross-family review of
   * this fix. Patching in place is only right while the edit can't have moved
   * the card. Under a title sort, renaming a book DOES move it, and the grid's
   * merge can upsert or append but never relocate a row — so an in-place patch
   * would pin the renamed book at its old position indefinitely. That case has
   * to drop the snapshot and rebuild in the server's order instead.
   */
  test('a rename under a title sort rebuilds the listing instead of pinning the card', async ({ page }) => {
    let title = OLD_TITLE;
    await mockLibrary(page, () => title);
    await mockEditEndpoints(page, (t) => { title = t; }, () => title);
    await page.addInitScript(() => localStorage.setItem('cwng:library-sort-v1', 'abc'));

    await page.goto('/app');
    const loadMore = page.getByRole('button', { name: 'Load more' });
    await expect(loadMore).toBeVisible();
    const firstPageCount = await gridBookLinks(page).count();
    await loadMore.click();
    await expect(gridBookLinks(page)).toHaveCount(firstPageCount * 2);

    const card = targetCard(page).first();
    await card.hover();
    await page.locator(`main a[href$="/book/${TARGET_ID}/edit"]`).first().click();
    await page.waitForURL(`**/book/${TARGET_ID}/edit`);
    await page.getByLabel(/^title$/i).first().fill(NEW_TITLE);
    await page.getByRole('button', { name: /save changes/i }).click();
    await page.waitForURL(`**/book/${TARGET_ID}`, { timeout: 15_000 });

    await page.goBack();
    await page.waitForURL(`**/book/${TARGET_ID}/edit`);
    await page.goBack();
    await page.waitForURL((u) => !/\/book\//.test(u.pathname));

    // The snapshot is gone, so the library starts again from page 1 rather
    // than restoring an accumulation the rename has invalidated the order of.
    await expect
      .poll(() => gridBookLinks(page).count(),
        { message: 'a sort-perturbing edit should rebuild the listing, not restore it' })
      .toBe(firstPageCount);
    // Still listed, and carrying the edit.
    await expect(targetCard(page)).toHaveCount(1);
    await expect(targetCard(page).first()).toContainText(NEW_TITLE);
  });
});
