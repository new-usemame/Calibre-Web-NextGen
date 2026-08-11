import { expect, test, type Page } from '@playwright/test';

/*
 * #325 — notes attached to web-reader highlights, and the in-reader
 * "Highlights and notes" drawer that lists them.
 *
 * Both live in one file on purpose. Each test drives a whole epub.js reader,
 * and this container starves when several do so at once — the same reason
 * reader-phase1 and reader-rtl each declare serial mode. Two spec files meant
 * four concurrent reader sessions and intermittent render timeouts that looked
 * like product failures; one serial file halves that.
 *
 * The backend has accepted `note_text` on create/edit since the annotation
 * subsystem landed; until this feature the SPA reader simply never sent or read
 * it. These tests drive the real reader against the real endpoints and assert on
 * server state, not on component internals.
 *
 * Deliberately content-agnostic: it annotates whatever text the reader happens
 * to render, so it cannot silently skip when the library's newest EPUB changes.
 *
 * Three things about this surface will waste your afternoon if you don't know
 * them (all measured, 2026-08-09):
 *
 *  1. Playwright's synthetic mouse DRAG hangs over the epub.js iframe —
 *     `mouse.down()` succeeds and the following `mouse.move()` never returns.
 *     Reproduced headless and headed. So selection is driven by building a
 *     Range inside the frame and dispatching the events epub.js listens for.
 *     epub.js binds those listeners from the PARENT frame, so this exercises
 *     the real `rendition.on('selected')` path rather than faking the outcome.
 *  2. epub.js paints highlights into a marks-pane SVG overlay that lives in the
 *     PARENT document, positioned over the iframe — NOT inside the book frame.
 *     Querying `.cwng-hl-noted` inside the frame always returns 0 and reads as
 *     "highlights are broken". Query the page.
 *  3. The first page of an EPUB is usually a text-free cover, and the reader
 *     restores a saved position. Page forward until there is real text before
 *     trying to select anything.
 *
 * LOAD SENSITIVITY, stated plainly. Each test renders a whole epub.js reader,
 * and cwn-local is shared. Measured on 2026-08-09: green at --workers=1 and
 * green under the CI retry policy, but under two workers on a busy container a
 * cold first render can miss its timeout and one test fails — a different one
 * each time. That is the same condition reader-rtl documents for itself, and it
 * is why the config sets retries in CI. If you see a single varying failure
 * here, re-run serially before suspecting the reader: a real regression fails
 * the same test every time.
 */

/*
 * Open the LOWEST-id book that has an EPUB and renders.
 *
 * Deliberately not "newest": sibling specs (reader-rtl, discover-upload) upload
 * fixtures that become the newest book, so targeting that made this spec both
 * annotate their fixtures and depend on their ordering. Low ids are the stable
 * seeded library.
 */
async function openReaderOnEpub(page: Page, offset: number): Promise<number | null> {
  /*
   * Choose a small-but-real EPUB, deterministically.
   *
   * Three constraints learned the hard way. Picking "the Nth book that happened
   * to render" slides the index whenever a render is slow, so two tests land on
   * the same book and each clears the other's fixtures. Picking purely by id
   * lands on Don Quixote, which does not render inside the timeout at all.
   * And resolving sizes for every book meant ~100 sequential requests per test,
   * a storm that itself caused the render timeouts and "socket hang up" it was
   * meant to avoid.
   *
   * So: take a bounded head of the id-sorted EPUB list, resolve just those
   * sizes, and order by size. The floor drops the synthetic single-page
   * fixtures that have no prose to select.
   */
  const MIN_BYTES = 60_000;
  // Wide enough that each of the SPEC_SLOTS lanes below still has more than one
  // candidate to fall back to, but still a bounded number of detail requests
  // (~16, against the ~100 that used to cause the very timeouts this avoids).
  const POOL = 16;
  const res = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
  const books = await res.json();
  const epubIds = [...(books.items || books.books || [])]
    .filter((bk: { formats?: string[] }) =>
      (bk.formats || []).some((f) => String(f).toLowerCase() === 'epub'))
    .sort((a: { id: number }, b: { id: number }) => a.id - b.id)
    .slice(0, POOL)
    .map((bk: { id: number }) => bk.id);

  const sized: { id: number; size: number }[] = [];
  for (const id of epubIds) {
    const detail = await (await page.request.get(`/api/v1/books/${id}`)).json();
    const epub = (detail.formats || []).find(
      (f: { format: string }) => f.format.toLowerCase() === 'epub');
    if (epub && (epub.size_bytes ?? 0) >= MIN_BYTES) sized.push({ id, size: epub.size_bytes });
  }
  sized.sort((a, b) => a.size - b.size || a.id - b.id);

  /*
   * Give each spec a DIFFERENT first choice, and every book as fallback.
   *
   * Two properties have to hold at once, and a stride filter (i % SLOTS ===
   * offset) satisfied only the first:
   *
   *   - two specs must not START on the same book, because each clears the
   *     other's annotations and then asserts on fixtures that just vanished;
   *   - a spec must always have somewhere to fall back to, because one cold
   *     render can miss even a generous timeout when workers share a container.
   *
   * The stride version selected NOTHING for the higher offsets whenever the
   * seeded library was small — `i % 4 === 3` over three eligible books is empty
   * — so mobile returned null before opening anything, deterministically, and
   * all three CI retries failed identically. That reads exactly like "the reader
   * cannot render an EPUB" while being purely an arithmetic bug here.
   *
   * Rotating the list instead is total: distinct starting points while there are
   * at least as many books as offsets, and the full list available after that.
   */
  if (!sized.length) {
    // Nothing cleared the size floor. Better to try the small fixtures than to
    // report "no EPUB renders", which sends the reader on a hunt for a bug that
    // is really an empty candidate list.
    console.warn(`[reader-notes] no EPUB >= ${MIN_BYTES}B; falling back to all EPUBs`);
    for (const id of epubIds) sized.push({ id, size: 0 });
  }
  const start = offset % sized.length;
  const candidates = [...sized.slice(start), ...sized.slice(0, start)];

  for (const candidate of candidates) {
    // Arrange the known state before the first render, not after it. A previous
    // run that died before cleanup leaves highlights behind, and then "the first
    // noted highlight on the page" is someone else's — which is exactly how this
    // spec once tapped a stale highlight and read its older note.
    await clearAnnotationsViaApi(page, candidate.id);
    // Retry the same book once before moving on: the first reader render in a
    // fresh context pays for the epub.js chunk and the book download at once.
    for (let attempt = 0; attempt < 2; attempt++) {
      await page.goto(`/app/read/${candidate.id}`);
      const rendered = await page.locator('iframe')
        .waitFor({ state: 'visible', timeout: 40_000 })
        .then(() => true).catch(() => false);
      if (rendered) return candidate.id;
    }
  }
  console.warn(`[reader-notes] none of ${candidates.length} candidate EPUB(s) rendered`);
  return null;
}

/** Page forward until the rendered section actually carries selectable text. */
/*
 * Wait for the reader to actually be ready, then find a page with prose.
 *
 * Deliberately condition-based rather than a fixed sleep. Under two concurrent
 * workers this container can take well over the few seconds a sleep assumes,
 * and starting to press ArrowRight against a half-rendered book was the cause
 * of every intermittent failure this spec had: it read an empty frame, paged
 * past the text, and reported "no page with selectable text" as though the
 * product were broken.
 */
async function pageUntilText(page: Page): Promise<boolean> {
  const frameText = async () => {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    if (!frame) return '';
    return await frame.evaluate(() => document.body?.innerText || '').catch(() => '');
  };
  // First: the book has rendered something at all (cover counts).
  const readyBy = Date.now() + 15_000;
  while (Date.now() < readyBy) {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    if (frame) {
      const painted = await frame.evaluate(
        () => (document.body?.innerText || '').length + document.querySelectorAll('img,svg,p,div').length,
      ).catch(() => 0);
      if (painted > 0) break;
    }
    await page.waitForTimeout(500);
  }
  // Then: page forward to prose, allowing each turn time to settle.
  for (let i = 0; i < 10; i++) {
    if ((await frameText()).trim().length > 300) return true;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(900);
  }
  return false;
}

/** Select a run of text the way a reader would — through epub.js's own handler. */
async function selectSomeText(page: Page): Promise<string> {
  const frame = page.frames().find((f) => f !== page.mainFrame())!;
  return await frame.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Node | null = null;
    while ((node = walker.nextNode())) if ((node.textContent || '').trim().length > 60) break;
    if (!node) return '';
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(70, (node.textContent || '').length));
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
    const box = range.getBoundingClientRect();
    for (const type of ['mousedown', 'mouseup'])
      document.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + 5, clientY: box.y + 5 }));
    return String(sel);
  });
}

const setNote = (page: Page, text: string) => page.evaluate((v) => {
  const ta = document.querySelector('textarea')!;
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!
    .set!.call(ta, v);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}, text);

const notesOnServer = (page: Page, bookId: number) => page.evaluate(async (id) => {
  const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
  return ((await r.json()).annotations || []).map((a: { note_text: string | null }) => a.note_text);
}, bookId);

// Highlights live in the parent document's marks-pane overlay (see header note).
const paintCounts = (page: Page) => page.evaluate(() => ({
  noted: document.querySelectorAll('.cwng-hl-noted').length,
  plain: document.querySelectorAll('.cwng-hl').length,
}));

const annotationIds = (page: Page, bookId: number) => page.evaluate(async (id) => {
  const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
  return ((await r.json()).annotations || []).map((a: { annotation_id: string }) => a.annotation_id);
}, bookId);

/*
 * Delete every annotation this spec added, leaving the book as it was found.
 * Not optional hygiene: these tests annotate whichever EPUB is newest, which is
 * routinely a fixture another spec owns (reader-rtl uploads one). Without this,
 * a sibling spec inherits our highlights and a moved reading position and fails
 * for reasons that have nothing to do with it.
 */
/* Clear a book's annotations over HTTP, with no page rendered. Used to arrange
 * state BEFORE opening the reader, which saves a whole render per test. */
async function clearAnnotationsViaApi(page: Page, bookId: number) {
  const csrf = (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token;
  const rows = (await (await page.request.get(`/annotations/${bookId}/data.json`)).json()).annotations || [];
  for (const a of rows) {
    await page.request.delete(`/annotations/${bookId}/${a.annotation_id}`, {
      headers: { 'X-CSRFToken': csrf },
    });
  }
}

async function restoreAnnotations(page: Page, bookId: number, keep: string[]) {
  await page.evaluate(async ([id, known]) => {
    const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
    const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
    const rows = (await r.json()).annotations || [];
    for (const a of rows) {
      if ((known as string[]).includes(a.annotation_id)) continue;
      await fetch(`/annotations/${id}/${a.annotation_id}`, {
        method: 'DELETE', credentials: 'include', headers: { 'X-CSRFToken': csrf },
      });
    }
  }, [bookId, keep] as [number, string[]]);
}

/** Tap a painted highlight, opening the edit popover. */
const tapHighlight = (page: Page, selector: string) => page.evaluate((sel) => {
  const g = document.querySelector(sel);
  if (!g) throw new Error('no painted highlight matching ' + sel);
  const box = g.getBoundingClientRect();
  for (const type of ['mousedown', 'mouseup', 'click'])
    g.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
}, selector);

/*
 * Page forward until the noted highlight is actually on screen.
 *
 * epub.js only paints an annotation into a view it has rendered, and the reader
 * restores a saved position — so "is it repainted after a reload" cannot be
 * asked of whatever page happens to be showing. At 375px the book repaginates
 * and the highlight lands on a different page than it does on desktop, so this
 * failed on mobile ONLY, while the feature worked correctly. The claim under
 * test is that the highlight comes back, not that it comes back on the page the
 * reader happened to open.
 */
async function pageUntilNotedPainted(page: Page, expected: number): Promise<number> {
  for (let i = 0; i < 12; i++) {
    const { noted } = await paintCounts(page);
    if (noted >= expected) return noted;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(900);
  }
  return (await paintCounts(page)).noted;
}

/*
 * Wait for the reader to render after a reload.
 *
 * Same budget as the first open in openReaderOnEpub, deliberately: a reload may
 * reuse the cached epub.js chunk but still re-downloads and re-parses the book,
 * so it is not the cheap operation a shorter timeout assumes. A 25s budget here
 * was the real cause of a "the noted highlight is repainted after a reload"
 * failure on mobile — the iframe never appeared, so the paint assertion below it
 * never ran, and the report named the repaint rather than the render.
 */
async function waitForReaderRender(page: Page): Promise<void> {
  await page.locator('iframe').waitFor({ state: 'visible', timeout: 40_000 });
}

/*
 * Wait until a reader setting is actually PERSISTED, before testing that it
 * persists.
 *
 * `persistSetting` debounces the save, so clicking a control and reloading races
 * the write: the UI updates instantly and the server may not have been told yet.
 * Locally the race went one way and in CI the other — the black-theme test
 * applied the theme correctly, reloaded before the save landed, and came back to
 * the previous theme, failing deterministically on both projects and every retry
 * while the feature worked.
 *
 * Polling the server closes it honestly: a test that a choice survives a reload
 * has to establish that the choice was saved, not assume it.
 */
async function waitForSavedSetting<K extends string>(
  page: Page, key: K, value: string,
): Promise<void> {
  await expect
    .poll(async () => {
      const res = await page.request.get('/api/v1/reader/settings');
      if (!res.ok()) return null;
      return ((await res.json()).reader ?? {})[key] ?? null;
    }, { timeout: 20_000, message: `reader setting ${key} should reach the server` })
    .toBe(value);
}

test.describe('reader notes (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('a note can be written, survives a reload, is editable and removable', async ({ page }, testInfo) => {
    // Renders the book three times (open, clear+reload, reload); the 45s default
    // is not enough for that when two workers share this container.
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);
    const before = await paintCounts(page);
    expect(await selectSomeText(page), 'text selected in the book frame').not.toBe('');

    // --- create: highlight + note in a single write ---
    await expect(page.getByRole('button', { name: 'Add note' })).toBeVisible();
    await page.getByRole('button', { name: 'Add note' }).click();
    await expect(page.locator('textarea')).toBeFocused();

    const NOTE = `Frame narrative established here. ${Date.now()}`;
    await setNote(page, NOTE);
    await page.getByRole('button', { name: 'Save note' }).click();

    await expect.poll(() => notesOnServer(page, bookId!)).toContain(NOTE);
    // Painted straight away, and marked as carrying a note.
    await expect.poll(async () => (await paintCounts(page)).noted).toBe(before.noted + 1);

    // --- survives a reload ---
    await page.reload();
    await waitForReaderRender(page);
    await expect(page.getByRole('button', { name: 'Highlights and notes' })).toBeVisible();
    expect(
      await pageUntilNotedPainted(page, before.noted + 1),
      'the noted highlight is repainted after a reload',
    ).toBe(before.noted + 1);
    expect(await notesOnServer(page, bookId!)).toContain(NOTE);

    // Tapping the highlight reveals the note without opening the composer.
    await tapHighlight(page, '.cwng-hl-noted');
    await expect(page.getByRole('dialog', { name: 'Highlight color' })).toContainText(NOTE);

    // --- edit ---
    await page.getByRole('button', { name: 'Edit note' }).click();
    const EDITED = `Edited: the narrator is introduced. ${Date.now()}`;
    await setNote(page, EDITED);
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect.poll(() => notesOnServer(page, bookId!)).toContain(EDITED);

    // --- remove the note; the highlight itself must survive ---
    await tapHighlight(page, '.cwng-hl-noted');
    await page.getByRole('button', { name: 'Edit note' }).click();
    await page.getByRole('button', { name: 'Remove note' }).click();

    await expect.poll(() => notesOnServer(page, bookId!)).not.toContain(EDITED);
    // The note marker is gone but the highlight is still painted.
    await expect.poll(async () => (await paintCounts(page)).noted).toBe(before.noted);
    expect((await paintCounts(page)).plain).toBe(before.plain + 1);

    await restoreAnnotations(page, bookId!, preExisting);
  });

  test('the one-tap colour highlight still creates without a note', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);
    const before = await paintCounts(page);
    const notesBefore = (await notesOnServer(page, bookId!)).length;
    await selectSomeText(page);
    await page.getByRole('button', { name: 'Green' }).click();

    // The colour tap must remain the fast path: a highlight with no note.
    await expect.poll(async () => (await paintCounts(page)).plain).toBe(before.plain + 1);
    const notes = await notesOnServer(page, bookId!);
    expect(notes.length).toBe(notesBefore + 1);
    expect(notes.filter((n: string | null) => !n).length).toBeGreaterThan(0);

    await restoreAnnotations(page, bookId!, preExisting);
  });
});

const drawer = (page: Page) => page.getByRole('navigation', { name: 'Highlights and notes' });

test.describe('reader highlights & notes drawer (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('lists a highlight with its note, and jumps to it', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 3 : 2);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);

    // Empty state is honest before anything exists.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    if (preExisting.length === 0) {
      await expect(drawer(page)).toContainText('No highlights yet');
    }
    await page.keyboard.press('Escape');

    // Make one, with a note.
    expect(await selectSomeText(page)).not.toBe('');
    await page.getByRole('button', { name: 'Add note' }).click();
    const NOTE = `Panel note ${Date.now()}`;
    await setNote(page, NOTE);
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    // It appears in the drawer, with its note, without a reload.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toBeVisible();
    await expect(drawer(page)).toContainText(NOTE);
    const rows = drawer(page).locator('li');
    await expect(rows).toHaveCount(preExisting.length + 1);

    // Jumping closes the drawer and moves the book.
    await rows.last().locator('button').click();
    await expect(drawer(page)).toBeHidden();

    // Survives a reload — the drawer is populated from the server, not memory.
    await page.reload();
    await waitForReaderRender(page);
    await expect(page.getByRole('button', { name: 'Highlights and notes' })).toBeVisible();
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toContainText(NOTE);

    await restoreAnnotations(page, bookId!, preExisting);
  });

  test('the drawer reflects a removed highlight without a reload', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 3 : 2);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);

    await selectSomeText(page);
    await page.getByRole('button', { name: 'Yellow' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length + 1);
    await page.keyboard.press('Escape');

    // Delete it through the reader, then re-open the drawer.
    await page.evaluate(() => {
      const g = document.querySelector('.cwng-hl')!;
      const box = g.getBoundingClientRect();
      for (const type of ['mousedown', 'mouseup', 'click'])
        g.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
    });
    await page.getByRole('button', { name: 'Remove highlight' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length);

    await restoreAnnotations(page, bookId!, preExisting);
  });
});

/*
 * Fullscreen. Asserts OUR wiring, not the browser's fullscreen implementation:
 * headless Chromium's real fullscreen is unreliable and it would be testing
 * Chrome rather than the reader. The Fullscreen API is stubbed so the test can
 * prove the button targets the reader shell and follows the browser's state.
 */
test.describe('reader fullscreen (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('the control targets the reader shell and follows the browser state', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

    // Record requests instead of entering real fullscreen.
    await page.evaluate(() => {
      (window as unknown as { __fsCalls: string[] }).__fsCalls = [];
      Element.prototype.requestFullscreen = function (this: Element) {
        (window as unknown as { __fsCalls: string[] }).__fsCalls.push(this.className);
        return Promise.resolve();
      };
    });

    const button = page.getByRole('button', { name: 'Full screen' });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute('aria-pressed', 'false');
    await button.click();

    // It asked for fullscreen on the reader shell — not the viewer, not <body>.
    const calls = await page.evaluate(() => (window as unknown as { __fsCalls: string[] }).__fsCalls);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain('reader');

    // The browser owns the state: until it reports fullscreen, the control must
    // not claim it. This is what breaks if someone "optimises" it to toggle its
    // own state optimistically — Escape would then leave the label lying.
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    // Once the browser does report it, the control flips to the exit affordance.
    await page.evaluate(() => {
      Object.defineProperty(document, 'fullscreenElement', {
        configurable: true,
        get: () => document.querySelector('[class*="reader"]'),
      });
      document.dispatchEvent(new Event('fullscreenchange'));
    });
    const exitButton = page.getByRole('button', { name: 'Exit full screen' });
    await expect(exitButton).toBeVisible();
    await expect(exitButton).toHaveAttribute('aria-pressed', 'true');
  });
});

/*
 * Touch targets on the reader toolbar (#325).
 *
 * The controls were 34x34 — above WCAG 2.2 SC 2.5.8's 24x24 floor, so nothing
 * flagged them, and under the 44x44 this project wants and every platform
 * guideline recommends. That combination is why it survived: conformant, and
 * still awkward with a thumb mid-page-turn.
 *
 * MEASURES AND HIT-TESTS, because the two can disagree. A box can report 44x44
 * while an overlay, clip or stacking context takes its edge, and the arithmetic
 * gives no hint when that happens — a sibling session measured an expander
 * claiming 44 and hit-testing at 40. So each control is probed at the four
 * inset corners of its own reported box.
 *
 * The narrow viewport is deliberate and is the case that actually broke: with
 * 44px buttons the toolbar has less slack, and because .bookTitle lacked
 * `min-width: 0` it refused to shrink, so the browser compressed the close
 * control to 16px wide on a 320px screen instead — smaller than before the
 * change was made to improve it.
 */
test.describe('reader toolbar touch targets (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('every toolbar control is at least 44x44 and receives a tap at its edges',
    async ({ page }, testInfo) => {
      test.setTimeout(120_000);
      const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 2 : 1);
      expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

      const NAMES = ['Close reader', 'Table of contents', 'Reading appearance',
                     'Highlights and notes', 'Full screen'];
      const undersized: string[] = [];
      const stolen: string[] = [];

      for (const name of NAMES) {
        const control = page.getByRole('button', { name }).first()
          .or(page.getByRole('link', { name }).first());
        // Full screen hides itself where the browser cannot support it, so a
        // missing control is legitimate rather than a failure.
        if (!(await control.isVisible().catch(() => false))) continue;
        const box = await control.boundingBox();
        expect(box, `${name} has a layout box`).not.toBeNull();
        if (box!.width < 44 || box!.height < 44) {
          undersized.push(`${name} (${Math.round(box!.width)}x${Math.round(box!.height)})`);
        }
        const corners = [[2, 2], [-2, 2], [2, -2], [-2, -2]].map(([dx, dy]) => ({
          x: dx > 0 ? box!.x + dx : box!.x + box!.width + dx,
          y: dy > 0 ? box!.y + dy : box!.y + box!.height + dy,
        }));
        const hits = await page.evaluate(({ pts, label }) => pts.map((p) => {
          const el = document.elementFromPoint(p.x, p.y);
          const owner = el?.closest('button, a');
          return owner?.getAttribute('aria-label') === label;
        }), { pts: corners, label: name });
        if (hits.some((h) => !h)) stolen.push(name);
      }

      expect(undersized, 'controls smaller than 44x44').toEqual([]);
      expect(stolen, 'controls whose own box does not receive the tap').toEqual([]);

      // The toolbar must absorb 44px controls by truncating the title, never by
      // scrolling the page sideways.
      const scrollsSideways = await page.evaluate(() =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
      expect(scrollsSideways, 'the reader page scrolls horizontally').toBe(false);
    });
});

/*
 * One column / two columns (#325).
 *
 * `spread` was already part of ReaderSettings and the classic reader has written
 * it for years — this reader hardcoded epub.js's `spread: 'auto'` and never read
 * it back, so a saved preference was silently discarded. The test therefore
 * checks the whole chain, not the button: the control changes the LAYOUT, and
 * the choice survives a reload and is re-applied.
 *
 * Asserts on epub.js's computed CSS columns inside the book frame rather than on
 * `aria-pressed`, because a control can be correctly "on" and do nothing — which
 * is exactly the bug being fixed. Deliberately does NOT clear annotations, so it
 * cannot destroy another spec's fixtures (F-08685b).
 */
test.describe('reader column count (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('choosing one column changes the layout and survives a reload',
    async ({ page }, testInfo) => {
      test.setTimeout(120_000);
      // Needs room for two-up to be possible at all; skip on the phone project,
      // where 'auto' is single-column regardless and the comparison is vacuous.
      test.skip(testInfo.project.name === 'mobile',
        'two-column layout needs a wide viewport; the comparison is vacuous on a phone');

      const bookId = await openReaderOnEpub(page, 0);
      expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

      const layout = () => page.evaluate(() => {
        const frame = document.querySelector('iframe');
        const doc = frame?.contentDocument;
        if (!doc) return null;
        const el = doc.querySelector('body') || doc.documentElement;
        return doc.defaultView!.getComputedStyle(el).columnWidth;
      });

      await page.getByRole('button', { name: 'Reading appearance' }).click();
      await expect(page.getByRole('button', { name: 'Two columns' })).toBeVisible();

      await page.getByRole('button', { name: 'Two columns' }).click();
      await expect.poll(layout).not.toBe(null);
      const twoUp = await layout();

      await page.getByRole('button', { name: 'One column' }).click();
      // Poll rather than sleep: epub.js re-lays-out asynchronously.
      await expect.poll(layout, { timeout: 15_000 }).not.toBe(twoUp);
      const oneUp = await layout();

      expect(oneUp, 'one column should be wider than a two-up column')
        .not.toBe(twoUp);

      // The preference is persisted server-side, so it must come back.
      await waitForSavedSetting(page, 'spread', 'nonespread');
      await page.reload();
      await waitForReaderRender(page);
      await page.getByRole('button', { name: 'Reading appearance' }).click();
      await expect(page.getByRole('button', { name: 'One column' }))
        .toHaveAttribute('aria-pressed', 'true');
      // ...and be APPLIED, not merely remembered by the button.
      await expect.poll(layout, { timeout: 15_000 }).toBe(oneUp);
    });
});

/*
 * The Black page theme (#325).
 *
 * Classic has had four page themes for years and stores the choice as
 * `blackTheme`; this reader mapped that value onto `dark`, so a reader who chose
 * Black got the warm near-black instead and had no way back to it. Same shape as
 * the column preference: a saved answer being quietly downgraded.
 *
 * Asserts that Black and Dark produce DIFFERENT grounds. Checking only that
 * Black "works" would pass if it were an alias for Dark, which is precisely the
 * bug — so the test has to compare the two.
 */
test.describe('reader black page theme (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('Black is a distinct pure-black ground, and it persists', async ({ page }) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, 1);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

    const ground = () => page.evaluate(() => {
      const doc = document.querySelector('iframe')?.contentDocument;
      const body = doc?.querySelector('body');
      if (!body || !doc?.defaultView) return null;
      return doc.defaultView.getComputedStyle(body).backgroundColor;
    });

    await page.getByRole('button', { name: 'Reading appearance' }).click();
    await expect(page.getByRole('button', { name: 'Black', exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Dark', exact: true }).click();
    await expect.poll(ground, { timeout: 15_000 }).not.toBe(null);
    const darkGround = await ground();

    await page.getByRole('button', { name: 'Black', exact: true }).click();
    await expect.poll(ground, { timeout: 15_000 }).toBe('rgb(0, 0, 0)');
    const blackGround = await ground();

    // The point of the feature: Black is not an alias for Dark.
    expect(blackGround, 'Black must differ from Dark').not.toBe(darkGround);

    // Stored as blackTheme server-side, so it must survive a reload -- both the
    // pressed state AND the actual ground, since the bug was that the value came
    // back and was then mapped onto something else.
    await waitForSavedSetting(page, 'theme', 'blackTheme');
    await page.reload();
    await waitForReaderRender(page);
    await expect.poll(ground, { timeout: 15_000 }).toBe('rgb(0, 0, 0)');
    await page.getByRole('button', { name: 'Reading appearance' }).click();
    await expect(page.getByRole('button', { name: 'Black', exact: true }))
      .toHaveAttribute('aria-pressed', 'true');
  });
});

/*
 * A standalone note in the highlights drawer (#325).
 *
 * A note ABOUT the book has no passage by design. Before this, the drawer drew
 * it as a BROKEN highlight: jump greyed out with "This highlight has no saved
 * position", and "(no text captured)" where the quote goes. Both sentences are
 * true of a highlight whose anchor a regenerated KEPUB destroyed, and false of
 * a note that never had one — and nothing in the row let a reader tell which
 * they were looking at. A deliberate state reported as a failure.
 *
 * Created through the API because the reader has no UI for making one yet; the
 * backend landed first on purpose. That is also why this is worth a test: the
 * rows can already exist before anything in the reader can produce them.
 */
test.describe('reader drawer: standalone notes (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('a note with no passage is not drawn as a broken highlight', async ({ page }) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, 3);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

    const NOTE = `A thought about the whole book. ${Date.now()}`;
    const created = await page.evaluate(async ([id, note]) => {
      const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
      const res = await fetch(`/annotations/${id}`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ position_type: 'unanchored', note_text: note }),
      });
      return { status: res.status, body: res.ok ? await res.json() : null };
    }, [String(bookId), NOTE] as const);
    expect(created.status, 'the backend accepts an unanchored note').toBe(201);
    expect(created.body.position_type).toBe('unanchored');

    await page.reload();
    await waitForReaderRender(page);
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    const row = drawer(page).locator('li', { hasText: NOTE });
    await expect(row).toBeVisible();

    // It must NOT claim a lost position or a missing quote — those describe a
    // damaged highlight, which this is not.
    await expect(row).not.toContainText('(no text captured)');
    const jump = row.getByRole('button');
    await expect(jump).toBeDisabled();
    await expect(jump).toHaveAttribute('title', 'A note about the book, not tied to a passage');

    // ...while an ordinary highlight in the same list still reads as one.
    await page.evaluate(async ([id]) => {
      const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
      for (const a of ((await (await fetch(`/annotations/${id}/data.json`, { credentials: 'include' })).json()).annotations || [])) {
        if (a.position_type === 'unanchored') {
          await fetch(`/annotations/${id}/${a.annotation_id}`, {
            method: 'DELETE', credentials: 'include', headers: { 'X-CSRFToken': csrf },
          });
        }
      }
    }, [String(bookId)] as const);  });
});

/*
 * Writing a note that is not attached to a passage (#325).
 *
 * The operator's ask was "see and do highlights and notes". A note about the
 * book — not about a sentence in it — had no way in at all: both readers require
 * you to select text first, which is the wrong shape for "the argument in
 * chapter 3 never lands".
 *
 * Drives the real flow rather than the API: open the drawer, click Write a note,
 * type, save. The API path is already covered by the drawer-rendering test; what
 * this adds is that a person can get there.
 */
test.describe('reader: writing a standalone note (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('a note can be written without selecting anything, and it lasts',
    async ({ page }) => {
      test.setTimeout(120_000);
      const bookId = await openReaderOnEpub(page, 1);
      expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

      const NOTE = `The frame narrative never pays off. ${Date.now()}`;

      await page.getByRole('button', { name: 'Highlights and notes' }).click();
      const start = page.getByRole('button', { name: 'Write a note' });
      await expect(start).toBeVisible();
      // Reachable with a thumb: this is a primary action on a phone.
      const box = await start.boundingBox();
      expect(box!.height, 'Write a note touch target').toBeGreaterThanOrEqual(44);

      await start.click();
      // The composer opens ready to type — no selection was made, so there is
      // nothing else the reader could want focused.
      await expect(page.locator('textarea')).toBeFocused();
      // ...and it must not offer highlight-only affordances for a note that has
      // no passage to colour.
      await expect(page.getByRole('dialog').getByRole('button', { name: 'Yellow' }))
        .toHaveCount(0);

      await setNote(page, NOTE);
      await page.getByRole('button', { name: 'Save note' }).click();

      // Stored as a genuinely unanchored row, not as a highlight with no text.
      await expect.poll(async () => await page.evaluate(async (id) => {
        const j = await (await fetch(`/annotations/${id}/data.json`, { credentials: 'include' })).json();
        return (j.annotations || []).filter((a: { position_type: string; note_text: string }) =>
          a.position_type === 'unanchored').map((a: { note_text: string }) => a.note_text);
      }, bookId!)).toContain(NOTE);

      // It survives a reload and reads as a note in the drawer.
      await page.reload();
      await waitForReaderRender(page);
      await page.getByRole('button', { name: 'Highlights and notes' }).click();
      const row = drawer(page).locator('li', { hasText: NOTE });
      await expect(row).toBeVisible();
      await expect(row).not.toContainText('(no text captured)');

      // Clean up after ourselves so the shared fixture is left as found.
      await page.evaluate(async (id) => {
        const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
        const j = await (await fetch(`/annotations/${id}/data.json`, { credentials: 'include' })).json();
        for (const a of (j.annotations || [])) {
          if (a.position_type === 'unanchored') {
            await fetch(`/annotations/${id}/${a.annotation_id}`, {
              method: 'DELETE', credentials: 'include', headers: { 'X-CSRFToken': csrf },
            });
          }
        }
      }, bookId!);
    });
});
