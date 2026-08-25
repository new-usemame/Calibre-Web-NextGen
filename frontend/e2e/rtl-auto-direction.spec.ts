import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #1073 regression — RTL titles, authors, series and descriptions must render
 * right-to-left in the new UI.
 *
 * Reported by @raphaelbahat with screenshots of the single-book view and the
 * shelf/catalog view: Arabic and Hebrew metadata rendered left-to-right, so the
 * text read from the wrong edge and trailing punctuation landed on the wrong
 * side. There was no `dir` attribute anywhere in the SPA or the classic
 * templates, so every string inherited the document's ltr direction.
 *
 * The fix puts dir="auto" on each text-bearing field, which resolves direction
 * from that string's own first strong directional character, and changes the
 * card's `text-align: left` to `start` so alignment follows the resolved
 * direction rather than overriding it.
 *
 * These specs fail on the pre-fix build: without dir="auto" the computed
 * direction on an Arabic title is "ltr", not "rtl".
 *
 * Seeded through the same endpoint the edit page uses, and restored in a
 * finally so the run leaves the library exactly as it found it.
 */

// Arabic; first strong character is RTL. "The Little Prince".
const RTL_TITLE = 'الأمير الصغير';
const RTL_AUTHOR = 'أنطوان دو سانت إكزوبيري';
const RTL_SERIES = 'سلسلة الكلاسيكيات';

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  const body = (await res.json()) as { csrf_token: string };
  return body.csrf_token;
}

interface Meta {
  title: string;
  authors: string;
  series?: string | null;
  series_index?: number | null;
}

async function readMeta(page: Page, id: number): Promise<Meta> {
  const res = await page.request.get(`/api/v1/books/${id}/metadata`);
  expect(res.ok(), 'metadata read should succeed').toBeTruthy();
  const m = (await res.json()) as Record<string, unknown>;
  return {
    title: String(m.title ?? ''),
    authors: String(m.authors ?? ''),
    series: (m.series as string | null) ?? null,
    series_index: (m.series_index as number | null) ?? null,
  };
}

async function writeMeta(page: Page, id: number, patch: Record<string, unknown>) {
  const res = await page.request.post(`/api/v1/books/${id}/metadata`, {
    headers: { 'X-CSRFToken': await csrfToken(page) },
    data: patch,
  });
  expect(res.ok(), `metadata write should succeed (got ${res.status()})`).toBeTruthy();
}

async function firstBookId(page: Page): Promise<number | null> {
  const res = await page.request.get('/api/v1/books?per_page=1');
  const body = (await res.json()) as { total: number; items: Array<{ id: number }> };
  if ((body.total ?? 0) < 1) return null;
  return body.items[0].id;
}

// Serial: every spec here renames the SAME book and restores it. In parallel
// they would race on each other's title rather than on the product.
test.describe.configure({ mode: 'serial' });

test.describe('#1073 automatic RTL text direction', () => {
  test('an Arabic title, author and series render RTL on the book page', async ({ page }) => {
    const errors = collectPageErrors(page);
    const bookId = await firstBookId(page);
    test.skip(bookId === null, 'no seeded book to rename');
    const id = bookId as number;

    const original = await readMeta(page, id);
    try {
      await writeMeta(page, id, {
        title: RTL_TITLE,
        authors: RTL_AUTHOR,
        series: RTL_SERIES,
        series_index: 1,
      });

      await page.goto(`/app/book/${id}`, { waitUntil: 'domcontentloaded' });

      const title = page.getByRole('heading', { level: 1 });
      await expect(title).toHaveText(RTL_TITLE);

      // The load-bearing assertion. Pre-fix this is "ltr" (inherited from the
      // document); post-fix dir="auto" resolves it from the Arabic text itself.
      const titleDir = await title.evaluate((el) => getComputedStyle(el).direction);
      expect(titleDir, 'Arabic <h1> title must compute RTL').toBe('rtl');

      const authorLine = page.locator('p').filter({ hasText: RTL_AUTHOR }).first();
      await expect(authorLine).toBeVisible();
      expect(
        await authorLine.evaluate((el) => getComputedStyle(el).direction),
        'Arabic author line must compute RTL',
      ).toBe('rtl');

      const seriesLine = page.locator('p').filter({ hasText: RTL_SERIES }).first();
      await expect(seriesLine).toBeVisible();
      expect(
        await seriesLine.evaluate((el) => getComputedStyle(el).direction),
        'Arabic series line must compute RTL',
      ).toBe('rtl');
    } finally {
      await writeMeta(page, id, {
        title: original.title,
        authors: original.authors,
        series: original.series,
        series_index: original.series_index,
      });
    }
    assertNoPageErrors(errors);
  });

  test('an Arabic card title renders RTL and sits flush to the right edge', async ({ page }) => {
    const bookId = await firstBookId(page);
    test.skip(bookId === null, 'no seeded book to rename');
    const id = bookId as number;

    const original = await readMeta(page, id);
    try {
      await writeMeta(page, id, { title: RTL_TITLE, authors: RTL_AUTHOR });

      await page.goto('/app');
      const cardTitle = page.locator('p').filter({ hasText: RTL_TITLE }).first();
      await expect(cardTitle).toBeVisible();

      expect(
        await cardTitle.evaluate((el) => getComputedStyle(el).direction),
        'Arabic card title must compute RTL',
      ).toBe('rtl');

      // Alignment, not just direction. `text-align: left` on the card control
      // would leave direction correct and the text still pinned to the left
      // edge — the exact half-fix this guards against. Measure the rendered
      // text run against its box: RTL text in a box wider than the text must
      // end flush right, not start flush left.
      const gap = await cardTitle.evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const text = range.getBoundingClientRect();
        const box = el.getBoundingClientRect();
        return { left: text.left - box.left, right: box.right - text.right,
                 slack: box.width - text.width };
      });
      // Only meaningful when the text is narrower than its box.
      if (gap.slack > 4) {
        expect(gap.right, 'RTL card title should be flush right, not left').toBeLessThan(gap.left);
      }
    } finally {
      await writeMeta(page, id, { title: original.title, authors: original.authors });
    }
  });

  test('the classic book page also renders an Arabic title RTL', async ({ page }) => {
    // The same defect existed in the classic templates, which are a separate
    // render path from the SPA and are what a user on the old theme still sees.
    // detail.html had no `dir` anywhere either, so this is the second face of
    // one root cause rather than a separate bug.
    const bookId = await firstBookId(page);
    test.skip(bookId === null, 'no seeded book to rename');
    const id = bookId as number;

    const original = await readMeta(page, id);
    try {
      await writeMeta(page, id, { title: RTL_TITLE, authors: RTL_AUTHOR });

      // No /app prefix: this is the classic server-rendered page.
      await page.goto(`/book/${id}`, { waitUntil: 'domcontentloaded' });
      const title = page.locator('h2#title');
      await expect(title).toHaveText(RTL_TITLE);
      expect(
        await title.evaluate((el) => getComputedStyle(el).direction),
        'Arabic title on the classic page must compute RTL',
      ).toBe('rtl');

      const author = page.locator('p.author').first();
      await expect(author).toBeVisible();
      expect(
        await author.evaluate((el) => getComputedStyle(el).direction),
        'Arabic author on the classic page must compute RTL',
      ).toBe('rtl');
    } finally {
      await writeMeta(page, id, { title: original.title, authors: original.authors });
    }
  });

  test('a Latin title still renders LTR', async ({ page }) => {
    // Guards the obvious over-correction: dir="auto" must resolve per string,
    // so switching a field to RTL for one book cannot flip everyone else.
    const bookId = await firstBookId(page);
    test.skip(bookId === null, 'no seeded book');
    const id = bookId as number;

    const original = await readMeta(page, id);
    try {
      await writeMeta(page, id, { title: 'The Little Prince', authors: original.authors });
      await page.goto(`/app/book/${id}`, { waitUntil: 'domcontentloaded' });
      const title = page.getByRole('heading', { level: 1 });
      await expect(title).toHaveText('The Little Prince');
      expect(
        await title.evaluate((el) => getComputedStyle(el).direction),
        'Latin title must stay LTR',
      ).toBe('ltr');
    } finally {
      await writeMeta(page, id, { title: original.title, authors: original.authors });
    }
  });
});
