import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * In-book links must stay in the book.
 *
 * The reported defect (iPhone Safari, 2026-09-12): tapping a footnote marker in
 * the web reader replaced the book with the CWNG library home page INSIDE the
 * reader's own content frame.
 *
 * MEASURED mechanism (this harness, all three engines, 2026-09-12):
 *   - epub.js renders each section into an iframe sandboxed WITHOUT
 *     allow-scripts. In WebKit — desktop Safari and iOS alike — such a frame
 *     receives NO DOM events at all, while native link activation still
 *     happens. So epub.js's own `link.onclick` interception never runs there,
 *     the frame follows `#fn-80-2` against its injected <base>, requests
 *     `/read/<id>/<section path>` from the server, and the server answered that
 *     unknown format with a redirect to the library home page.
 *   - Chromium delivers the events normally, which is why this was invisible on
 *     the desktop lane for as long as it shipped.
 *
 * The spec therefore runs on a REAL WebKit touch device (the reported engine)
 * and on the desktop project, and it taps SCREEN COORDINATES rather than
 * calling a handler, so whichever layer is supposed to receive the activation
 * has to actually receive it.
 *
 * The fixture is served in place of whatever EPUB the seeded library holds, so
 * the assertions name exact strings (NOTE-EPUB-TEXT-ALPHA, …) instead of
 * hoping the catalogue still contains a book with a footnote.
 */

const EPUB_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/test_noteref_links.epub',
);

test.describe.configure({ mode: 'serial' });

async function findEpubBookId(page: Page): Promise<number> {
  const res = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  const list = (await res.json()) as { items?: { id: number; formats?: string[] }[] };
  const found = (list.items || []).find((b) => (b.formats || []).some((f) => f.toLowerCase() === 'epub'));
  if (!found) throw new Error('no epub book in the library to host the fixture');
  return found.id;
}

/** Open the reader on the fixture EPUB, at its first section, with no saved position. */
async function openFixtureReader(page: Page): Promise<number> {
  const id = await findEpubBookId(page);
  await page.route('**/show/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/epub+zip', path: EPUB_FIXTURE }),
  );
  const csrf = await page.request
    .get('/api/v1/auth/csrf')
    .then((r) => r.json())
    .then((b: { csrf_token: string }) => b.csrf_token);
  await page.request.post(`/api/v1/books/${id}/bookmark`, {
    headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
    data: { format: 'epub', bookmark: '' },
  });

  await page.goto(`/app/read/${id}`);
  await expect(page.locator('iframe')).toBeAttached({ timeout: 30_000 });
  await expect.poll(() => frameText(page), { timeout: 30_000 }).not.toBe('');

  /*
   * Then go to the first section through the reader's own table of contents,
   * as reader-rtl does, instead of trusting where the book opens.
   *
   * Clearing the bookmark above is necessary but not sufficient: the reader
   * saves its position on a debounce, so a position written by a PREVIOUS test
   * (this spec's own cross-document link test ends in section 2) can land on
   * the server just after this test cleared it. The table of contents is not
   * the control under test, and it cannot be defeated by a late write.
   */
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  await page.getByRole('button', { name: 'Noteref Chapter', exact: true }).click();
  await expect.poll(() => frameText(page), { timeout: 20_000 }).toContain('NOTEREF-SECTION-1');
  return id;
}

async function frameText(page: Page): Promise<string> {
  return page.evaluate(() => {
    const frame = document.querySelector('iframe') as HTMLIFrameElement | null;
    try {
      return frame?.contentDocument?.body?.innerText ?? '';
    } catch {
      return '';
    }
  });
}

async function frameUrl(page: Page): Promise<string> {
  return page.evaluate(() => {
    const frame = document.querySelector('iframe') as HTMLIFrameElement | null;
    try {
      return frame?.contentDocument?.location?.href ?? '';
    } catch {
      return 'CROSS-ORIGIN';
    }
  });
}

/*
 * Viewport coordinates of a book anchor, in the PARENT page's coordinate space.
 *
 * The FIRST client rect, not the bounding box: a link that wraps across two
 * lines has a bounding box whose centre falls in the gap between them, where
 * neither the link nor its hit target is drawn.
 */
async function anchorPoint(page: Page, selector: string): Promise<{ x: number; y: number } | null> {
  const point = await page.evaluate((sel) => {
    const frame = document.querySelector('iframe') as HTMLIFrameElement | null;
    const doc = frame?.contentDocument;
    const anchor = doc?.querySelector(sel) as HTMLElement | null;
    if (!frame || !anchor) return null;
    const rect = anchor.getClientRects()[0];
    if (!rect || (rect.width === 0 && rect.height === 0)) return null;
    const frameRect = frame.getBoundingClientRect();
    return {
      x: frameRect.left + rect.left + rect.width / 2,
      y: frameRect.top + rect.top + rect.height / 2,
    };
  }, selector);
  return point;
}

function onScreen(page: Page, point: { x: number; y: number } | null): boolean {
  if (!point) return false;
  const size = page.viewportSize();
  if (!size) return point.x >= 0 && point.y >= 0;
  return point.x >= 0 && point.y >= 0 && point.x <= size.width && point.y <= size.height;
}

/** The reader's transparent hit target for one of the book's links. */
const linkHit = (page: Page, href: string) =>
  page.locator(`[data-testid="reader-link-hit"][data-href="${href}"]`).first();

/*
 * Tap a link the way a reader does: on the glass, over the words.
 *
 * The reader paints a transparent hit target over every link on the visible
 * page, in the PARENT document — in WebKit the sandboxed book frame receives
 * no events to intercept, so that overlay is the layer that has to work. The
 * tap goes to that element, which also means Playwright waits for it to stop
 * moving: a freshly rendered section keeps settling (typography, margins, late
 * fonts) for about a second, and a raw coordinate measured before it settles
 * is a coordinate the link has since walked away from.
 *
 * How much of a chapter fits on a page depends on viewport and typography, so
 * a link that needs no page turn at 1280px may need one at 375px. Turning the
 * page is also what most needs covering: hit targets are measured per page, so
 * they must be re-measured on every relocation or they rot in place.
 */
async function tapLink(page: Page, href: string): Promise<void> {
  const selector = `a[href="${href}"]`;
  const hit = linkHit(page, href);
  for (let turn = 0; turn < 8; turn += 1) {
    if (await hit.count()) break;
    if (onScreen(page, await anchorPoint(page, selector))) {
      // On the page, but not measured yet: the reader keeps re-measuring for
      // about a second after a section settles. Wait once, then tap whatever
      // is there.
      await page.waitForTimeout(1000);
      break;
    }
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(700);
  }

  const hasTouch = await page.evaluate(() => 'ontouchstart' in window || navigator.maxTouchPoints > 0);
  if (await hit.count()) {
    if (hasTouch) await hit.tap();
    else await hit.click();
    return;
  }

  /*
   * No hit target. Tap the link itself, at its place on the glass, and let the
   * test's own assertions judge what the reader does — this spec is about what
   * a reader experiences, not about which of the layers caught the tap. (This
   * is also the path that reproduces the original defect against unfixed code,
   * rather than failing early on a missing overlay.)
   */
  const point = await anchorPoint(page, selector);
  expect(point, `the link to ${href} should be on the visible page`).not.toBeNull();
  const { x, y } = point as { x: number; y: number };
  if (hasTouch) await page.touchscreen.tap(x, y);
  else await page.mouse.click(x, y);
}

const noteSheet = (page: Page) => page.getByTestId('reader-note-sheet');

/*
 * Whatever the reader does with a link, it must never do THIS: put a page of the
 * app inside the book frame. The library shell is recognisable by its own
 * navigation landmarks, which no EPUB section contains.
 */
async function expectNoAppShellInFrame(page: Page): Promise<void> {
  const inner = await page.evaluate(() => {
    const frame = document.querySelector('iframe') as HTMLIFrameElement | null;
    try {
      const doc = frame?.contentDocument;
      if (!doc) return { html: '', url: '' };
      return { html: doc.documentElement.outerHTML.slice(0, 4000), url: doc.location.href };
    } catch {
      return { html: '', url: 'CROSS-ORIGIN' };
    }
  });
  expect(inner.html).not.toContain('id="root"');
  expect(inner.html.toLowerCase()).not.toContain('calibre-web');
  expect(inner.url).not.toMatch(/\/(?:app|login)\b/);
  expect(inner.url).not.toMatch(/\/$/);
}

test.describe('in-book links stay in the reader', () => {
  test('an EPUB 3 noteref opens the note without leaving the section', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);
    const before = await frameUrl(page);

    await tapLink(page, '#fn-80-2');

    await expect(noteSheet(page)).toBeVisible({ timeout: 10_000 });
    await expect(noteSheet(page)).toContainText('NOTE-EPUB-TEXT-ALPHA');
    // The backlink is the note's way home in a scrolling reader; in a popup it
    // is a dead end, so it is stripped rather than rendered.
    expect(await noteSheet(page).locator('a').count()).toBe(0);

    expect(await frameUrl(page)).toBe(before);
    expect(await frameText(page)).toContain('NOTEREF-SECTION-1');
    await expectNoAppShellInFrame(page);
  });

  test('a DPUB-ARIA noteref opens the note, sanitised', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);

    await tapLink(page, '#fn-aria');

    await expect(noteSheet(page)).toBeVisible({ timeout: 10_000 });
    await expect(noteSheet(page)).toContainText('NOTE-ARIA-TEXT-BETA');
    // Inline markup survives; the note is text, not a screenshot of text.
    expect(await noteSheet(page).locator('em').count()).toBe(1);

    /*
     * The popup lifts book markup into the APP's document, where scripting IS
     * enabled — the one place the sandbox does not protect the user. The
     * fixture note carries a <script>, an onerror <img> and an inline onclick;
     * none of them may survive the trip.
     */
    expect(await noteSheet(page).locator('script, img').count()).toBe(0);
    await noteSheet(page).getByText('NOTE-HANDLER-PARAGRAPH').click();
    const fired = await page.evaluate(() => ({
      script: !!(window as unknown as Record<string, unknown>).NOTE_SCRIPT_RAN,
      img: !!(window as unknown as Record<string, unknown>).NOTE_IMG_ONERROR_RAN,
      handler: !!(window as unknown as Record<string, unknown>).NOTE_HANDLER_RAN,
    }));
    expect(fired).toEqual({ script: false, img: false, handler: false });
  });

  test('"Go to note" relocates the reader to the note itself', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);

    await tapLink(page, '#fn-80-2');
    await expect(noteSheet(page)).toBeVisible({ timeout: 10_000 });
    await noteSheet(page).getByRole('button', { name: /go to note/i }).click();

    await expect(noteSheet(page)).toBeHidden();
    /*
     * "Relocated" means the note is ON THE PAGE — not merely somewhere in the
     * section's DOM, which it was before the tap too. So assert on layout.
     */
    await expect
      .poll(() => anchorPoint(page, '#fn-80-2').then((p) => onScreen(page, p)), { timeout: 20_000 })
      .toBe(true);
    await expectNoAppShellInFrame(page);
  });

  test('a cross-document noteref shows the note from the other section', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);

    await tapLink(page, 'ch2.xhtml#fn-cross');

    await expect(noteSheet(page)).toBeVisible({ timeout: 15_000 });
    await expect(noteSheet(page)).toContainText('NOTE-CROSS-TEXT-GAMMA');
    // The reader stayed where the reader was.
    expect(await frameText(page)).toContain('NOTEREF-SECTION-1');
    await expectNoAppShellInFrame(page);
  });

  test('a plain cross-document link relocates inside the reader', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);

    await tapLink(page, 'ch2.xhtml');

    await expect.poll(() => frameText(page), { timeout: 20_000 }).toContain('NOTEREF-SECTION-2');
    await expect(noteSheet(page)).toHaveCount(0);
    await expectNoAppShellInFrame(page);
    // Still the reader, not a full-page navigation to the section file.
    expect(page.url()).toContain('/app/read/');
  });

  test('an external link never navigates the book frame', async ({ page }) => {
    test.setTimeout(120_000);
    await openFixtureReader(page);
    const before = await frameUrl(page);

    await tapLink(page, 'https://example.org/cwng-external');
    await page.waitForTimeout(2000);

    expect(await frameUrl(page)).toBe(before);
    expect(await frameText(page)).toContain('NOTEREF-SECTION-1');
    expect(page.url()).toContain('/app/read/');
    await expectNoAppShellInFrame(page);
  });
});
