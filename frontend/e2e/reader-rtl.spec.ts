import { expect, test, type Page } from '@playwright/test';

/**
 * #1303 — page turns in a right-to-left book.
 *
 * Japanese and Traditional Chinese books declare `page-progression-direction="rtl"`
 * on the EPUB spine, which means forward runs LEFTWARD. epub.js already lays the
 * book out that way, but `next()`/`prev()` stay spine-forward/spine-backward in
 * both directions, so the reader has to decide which screen side each one belongs
 * on. It didn't: left was hard-wired to `prev()` and right to `next()`, so tapping
 * the side a Japanese reader taps to advance took them backwards instead.
 *
 * The fixture is `tests/fixtures/sample_books/test_rtl_vertical.epub`, seeded by
 * the e2e workflow. The skip below is for a local library that has not been
 * seeded — in CI the seed step itself fails loudly if the book is missing, so
 * this can't quietly turn the gate off.
 */

const RTL_TITLE = 'RTL Vertical Sample';

async function findBookIdByTitle(page: Page, title: string): Promise<number | null> {
  const response = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  const payload = await response.json();
  const books = (payload.items || payload.books || []) as { id: number; title: string }[];
  return books.find((book) => book.title === title)?.id ?? null;
}

/**
 * Drop any saved reading position for this book, so the reader opens at the
 * first section. Without this the spec is not idempotent: the reader restores
 * the bookmark, so a previous run that paged to section 3 leaves the next run
 * opening at section 3 and timing out waiting for section 1. The bookmark route
 * treats an empty `bookmark` as a clear.
 */
async function clearSavedPosition(page: Page, bookId: number) {
  const csrf = await page.request.get('/api/v1/auth/csrf')
    .then((r) => r.json() as Promise<{ csrf_token: string }>)
    .then((b) => b.csrf_token);
  await page.request.post(`/api/v1/books/${bookId}/bookmark`, {
    headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
    data: { format: 'epub', bookmark: '' },
  });
}

/**
 * Open the reader at the start of the book and wait for its first section to
 * paint. The two waits have to fit inside the 45s per-test timeout together, or
 * a genuine failure would surface as an unhelpful "test timed out" instead of
 * naming what it was waiting for.
 */
async function openReader(page: Page, bookId: number, firstSectionText: RegExp) {
  await clearSavedPosition(page, bookId);
  await page.goto(`/app/read/${bookId}`);
  await page.locator('iframe').first().waitFor({ state: 'visible', timeout: 20_000 });
  await expect(page.frameLocator('iframe').first().locator('body'))
    .toContainText(firstSectionText, { timeout: 20_000 });
}

/** The x of a nav zone, identified by the action it says it performs. */
async function zoneX(page: Page, label: 'Next page' | 'Previous page'): Promise<number> {
  const box = await page.getByRole('button', { name: label, exact: true }).boundingBox();
  if (!box) throw new Error(`nav zone "${label}" has no box`);
  return box.x;
}

test.describe('reader page-turn direction', () => {
  // Serial, as in reader-phase1.spec.ts: each case downloads and renders a whole
  // book, and running them against one container concurrently starves the
  // rendition long enough to time out on load rather than on anything real.
  test.describe.configure({ mode: 'serial' });

  test('a right-to-left book turns forward on the LEFT of the screen', async ({ page }) => {
    await page.goto('/app');
    const bookId = await findBookIdByTitle(page, RTL_TITLE);
    test.skip(bookId === null, `library has no "${RTL_TITLE}" — seed test_rtl_vertical.epub`);

    await openReader(page, bookId!, /RTL-SECTION-1/);
    const body = page.frameLocator('iframe').first().locator('body');

    // The forward control sits on the left, the back control on the right.
    // Labels travel with the action, so a screen reader announces what the
    // button actually does rather than which side it is on.
    expect(await zoneX(page, 'Next page')).toBeLessThan(await zoneX(page, 'Previous page'));

    // Forward really is forward: 1 -> 2 -> 3 through the spine.
    await page.getByRole('button', { name: 'Next page', exact: true }).click();
    await expect(body).toContainText(/RTL-SECTION-2/, { timeout: 15_000 });
    await page.getByRole('button', { name: 'Next page', exact: true }).click();
    await expect(body).toContainText(/RTL-SECTION-3/, { timeout: 15_000 });

    // And the right-hand zone goes back.
    await page.getByRole('button', { name: 'Previous page', exact: true }).click();
    await expect(body).toContainText(/RTL-SECTION-2/, { timeout: 15_000 });
  });

  test('arrow keys follow the same side convention in a right-to-left book', async ({ page }) => {
    await page.goto('/app');
    const bookId = await findBookIdByTitle(page, RTL_TITLE);
    test.skip(bookId === null, `library has no "${RTL_TITLE}" — seed test_rtl_vertical.epub`);

    await openReader(page, bookId!, /RTL-SECTION-1/);
    const body = page.frameLocator('iframe').first().locator('body');

    await page.keyboard.press('ArrowLeft');
    await expect(body).toContainText(/RTL-SECTION-2/, { timeout: 15_000 });

    await page.keyboard.press('ArrowRight');
    await expect(body).toContainText(/RTL-SECTION-1/, { timeout: 15_000 });
  });

  test('a left-to-right book still turns forward on the RIGHT', async ({ page }) => {
    // The control that would catch a blanket flip: only RTL books may swap.
    await page.goto('/app');
    const response = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
    const payload = await response.json();
    const books = (payload.items || payload.books || []) as { id: number; title: string }[];

    let checked = false;
    // Capped: a handful of candidates is enough to find one readable EPUB, and
    // an uncapped sweep would open every book in the library on a miss.
    for (const book of books.filter((b) => b.title !== RTL_TITLE).slice(0, 5)) {
      await page.goto(`/app/read/${book.id}`);
      const rendered = await page.locator('iframe').first()
        .waitFor({ state: 'visible', timeout: 8_000 }).then(() => true).catch(() => false);
      if (!rendered) continue;
      await expect(page.getByRole('button', { name: 'Next page', exact: true })).toBeVisible();
      expect(await zoneX(page, 'Previous page')).toBeLessThan(await zoneX(page, 'Next page'));
      checked = true;
      break;
    }
    test.skip(!checked, 'no loadable left-to-right EPUB in this library');
  });
});
