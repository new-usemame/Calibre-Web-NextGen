import { expect, test, type Page } from '@playwright/test';

/*
 * #325 — notes attached to web-reader highlights.
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
 */

/*
 * Open the LOWEST-id book that has an EPUB and renders.
 *
 * Deliberately not "newest": sibling specs (reader-rtl, discover-upload) upload
 * fixtures that become the newest book, so targeting that made this spec both
 * annotate their fixtures and depend on their ordering. Low ids are the stable
 * seeded library.
 */
async function openReaderOnEpub(page: Page): Promise<number | null> {
  const res = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
  const books = await res.json();
  const candidates = [...(books.items || books.books || [])]
    .sort((a: { id: number }, b: { id: number }) => a.id - b.id);
  for (const bk of candidates) {
    const detail = await (await page.request.get(`/api/v1/books/${bk.id}`)).json();
    const hasEpub = (detail.formats || []).some(
      (f: { format: string }) => f.format.toLowerCase() === 'epub');
    if (!hasEpub) continue;
    await page.goto(`/app/read/${bk.id}`);
    const rendered = await page.locator('iframe').waitFor({ state: 'visible', timeout: 8000 })
      .then(() => true).catch(() => false);
    if (rendered) { await page.waitForTimeout(4000); return bk.id; }
  }
  return null;
}

/** Page forward until the rendered section actually carries selectable text. */
async function pageUntilText(page: Page): Promise<boolean> {
  for (let i = 0; i < 12; i++) {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    const len = frame
      ? await frame.evaluate(() => (document.body?.innerText || '').trim().length).catch(() => 0)
      : 0;
    if (len > 300) return true;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(1200);
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

test.describe('reader notes (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('a note can be written, survives a reload, is editable and removable', async ({ page }) => {
    const bookId = await openReaderOnEpub(page);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
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
    await page.waitForTimeout(5000);
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

  test('the one-tap colour highlight still creates without a note', async ({ page }) => {
    const bookId = await openReaderOnEpub(page);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
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
