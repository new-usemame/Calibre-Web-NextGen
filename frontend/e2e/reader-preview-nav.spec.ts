import { test, expect, type Page } from '@playwright/test';

/*
 * Jumping to a saved highlight must not move the reader's saved place.
 *
 * epub.js reports a programmatic `display()` as a relocation exactly like a page
 * turn, and the reader persisted every relocation. So opening a highlight to
 * re-read it overwrote the bookmark with the highlight's position -- and because
 * the same save carries a percentage, and the server finishes a book at 99%
 * (FINISHED_PERCENT_THRESHOLD, kosync.py), opening a highlight near the end of a
 * book marked the whole book FINISHED and pushed that to Kobo sync and
 * Hardcover.
 *
 * This drives the real flow rather than the handler, because the defect is in
 * the WIRING -- which relocations are treated as the reader's own. A unit test
 * over the decision function cannot see whether `goToAnnotation` actually arms
 * the flag or whether a page turn actually clears it, and those are the two
 * parts most likely to rot.
 */

const BOOKMARK = (id: number) => `/api/v1/books/${id}/bookmark?format=epub`;

/** The saved reading position the SERVER holds -- the thing that survives the
 *  tab closing, and so the only honest subject for these assertions. */
async function savedPosition(page: Page, bookId: number): Promise<string | null> {
  const res = await page.request.get(BOOKMARK(bookId));
  if (!res.ok()) return null;
  return (await res.json()).bookmark ?? null;
}

/*
 * Wait for the saved position to become something other than `previous`.
 *
 * Never a fixed sleep: the save is debounced 800ms and then goes to the server,
 * so a sleep either flakes under load or pads every run. Polling the server
 * asserts the thing we actually care about -- that the write landed -- instead
 * of guessing how long it takes.
 */
async function waitForSavedChange(
  page: Page, bookId: number, previous: string | null, timeoutMs = 15_000,
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  let seen = previous;
  while (Date.now() < deadline) {
    seen = await savedPosition(page, bookId);
    if (seen && seen !== previous) return seen;
    await page.waitForTimeout(250);
  }
  return seen;
}

/*
 * Pick an EPUB with enough prose to page through, DIFFERENT per project.
 *
 * Small-but-real: the tiny synthetic fixtures have a single page, so paging
 * cannot produce two distinct positions and the test would assert nothing.
 *
 * The rotation is not decoration. This spec clears the book's bookmark and
 * annotations to arrange its state, so two projects running concurrently
 * (desktop and mobile do, `fullyParallel`) on the SAME book each wipe the other's
 * fixtures mid-run. Observed exactly that: desktop passed and mobile timed out
 * waiting for the reader to render. Rotating by project gives each lane its own
 * first choice while leaving every book available as a fallback.
 */
async function pickEpub(page: Page, offset = 0): Promise<number | null> {
  const MIN_BYTES = 60_000;
  const res = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
  const books = await res.json();
  const ids = [...(books.items || books.books || [])]
    .filter((bk: { formats?: string[] }) =>
      (bk.formats || []).some((f) => String(f).toLowerCase() === 'epub'))
    .sort((a: { id: number }, b: { id: number }) => a.id - b.id)
    .slice(0, 16)
    .map((bk: { id: number }) => bk.id);

  const sized: { id: number; size: number; url: string }[] = [];
  for (const id of ids) {
    const detail = await (await page.request.get(`/api/v1/books/${id}`)).json();
    const epub = (detail.formats || []).find(
      (f: { format: string }) => f.format.toLowerCase() === 'epub');
    if (epub && (epub.size_bytes ?? 0) >= MIN_BYTES && epub.download_url) {
      sized.push({ id, size: epub.size_bytes, url: epub.download_url });
    }
  }
  if (!sized.length) return null;
  sized.sort((a, b) => a.size - b.size || a.id - b.id);

  /*
   * Rotate rather than stride: a stride (i % lanes === offset) selects NOTHING
   * for higher offsets on a small library, which fails deterministically and
   * reads like "the reader cannot render an EPUB".
   *
   * Then PROBE the file. A book can have a perfectly good format row whose file
   * is not on disk -- this library has one -- and the reader answers that with
   * "Could not load the book file (404)" and no iframe. Picking it produces a
   * 40s render timeout that looks exactly like a slow book or a broken reader,
   * which is how it cost a debugging round here. The list is the catalogue; only
   * a fetch tells you the book is really there.
   */
  const rotated = [...sized.slice(offset % sized.length), ...sized.slice(0, offset % sized.length)];
  for (const cand of rotated) {
    const probe = await page.request.get(cand.url);
    if (probe.ok()) return cand.id;
  }
  return null;
}

async function csrfToken(page: Page): Promise<string> {
  return (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token;
}

/*
 * Remove every annotation on this book, one call each.
 *
 * There is no bulk delete -- the only DELETE is
 * `/annotations/<book_id>/<annotation_id>`. This matters more than it looks: the
 * test selects the highlight row by role, so a leftover row from an earlier run
 * would make `.first()` pick an arbitrary highlight and the assertion would be
 * about a position this test never established.
 */
async function clearAnnotations(page: Page, bookId: number) {
  const csrf = await csrfToken(page);
  const rows = (await (await page.request.get(`/annotations/${bookId}/data.json`)).json()).annotations || [];
  for (const a of rows) {
    await page.request.delete(`/annotations/${bookId}/${a.annotation_id}`, {
      headers: { 'X-CSRFToken': csrf },
    });
  }
}

/*
 * Wait until the saved position stops moving, and return it.
 *
 * Opening a book is not a single write. epub.js re-displays the saved CFI and
 * then reports the location of the page it actually laid out, which is the
 * page-START cfi -- a normalized neighbour of what was saved, not the same
 * string (`/4/18/2/10/2/1:0` comes back as `/4/8[pgepubid00000]/1:0`). That
 * write lands after the iframe is visible, so a baseline read at render time is
 * read too early and drifts underneath the assertion.
 *
 * This is pre-existing reader behaviour, not something the preview flag
 * changed; the test just has to measure the settled value rather than assume
 * one.
 */
async function waitForSavedStable(page: Page, bookId: number, timeoutMs = 15_000): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  let last = await savedPosition(page, bookId);
  let stableSince = Date.now();
  while (Date.now() < deadline) {
    await page.waitForTimeout(400);
    const now = await savedPosition(page, bookId);
    if (now !== last) {
      last = now;
      stableSince = Date.now();
    } else if (Date.now() - stableSince >= 2_000) {
      return last;
    }
  }
  return last;
}

/*
 * The spine section of a CFI -- the `/6/N` before the `!`.
 *
 * Assertions compare SECTIONS, not whole CFIs, and that is deliberate. Within a
 * section the reader legitimately rewrites the saved CFI as the layout settles
 * (the exact offset a page starts at moves), so an exact-string comparison
 * flakes on the reader doing something correct. What the defect actually does is
 * move the saved position into the HIGHLIGHT'S section -- a change this catches
 * and normalization noise cannot fake.
 */
function spineSection(cfi: string | null): string | null {
  if (!cfi) return null;
  const m = /^epubcfi\((\/\d+(?:\/\d+)*)!/.exec(cfi);
  return m ? m[1] : null;
}

async function turnPage(page: Page, times = 1) {
  for (let i = 0; i < times; i += 1) {
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(400);
  }
}

test.describe('reader: a jump is not a reading position', () => {
  test('opening a saved highlight leaves the saved place alone, and reading on saves again', async ({ page }, testInfo) => {
    /*
     * Desktop only, and that is a deliberate scope, not an oversight.
     *
     * This spec arranges SERVER state keyed by (user, book) -- the reading
     * position -- and the reading position is exactly what it asserts on. Two
     * projects running concurrently therefore overwrite each other's subject
     * unless each gets its own book, and this library does not reliably have
     * enough books with real files on disk to guarantee that (several rows have
     * no file at all). Lane rotation collapses onto one book and the projects
     * silently clobber each other.
     *
     * The invariant itself is viewport-independent: whether a relocation is
     * persisted is decided in the same handler regardless of screen size, and
     * nothing about it is layout-dependent. The mobile-specific reader concerns
     * -- target sizes, focus, drawer behaviour -- are covered in
     * reader-notes.spec.ts, which does not arrange per-book server state.
     */
    test.skip(
      testInfo.project.name !== 'desktop',
      'arranges per-(user,book) server state; concurrent projects would clobber it',
    );
    const bookId = await pickEpub(page, 0);
    test.skip(!bookId, 'no EPUB with enough prose in this library');

    // Start from a known-clean position so a previous run cannot supply the
    // value this test is about to assert on.
    const csrf = await csrfToken(page);
    await page.request.post(`/api/v1/books/${bookId}/bookmark`, {
      data: { format: 'epub', bookmark: '' },
      headers: { 'X-CSRFToken': csrf },
    });
    await clearAnnotations(page, bookId!);

    // `/app/read/<id>` is the SPA reader. `/read/<id>` is the CLASSIC reader and
    // renders a perfectly good page with no epub.js iframe -- so getting this
    // wrong fails on the render wait, 40s later, looking like a broken reader.
    await page.goto(`/app/read/${bookId}`);
    await page.locator('iframe').waitFor({ state: 'visible', timeout: 40_000 });

    // Read forward to a real position A, and let it save.
    await turnPage(page, 3);
    const positionA = await waitForSavedChange(page, bookId!, null);
    expect(positionA, 'reading should save a position before we test preserving it').toBeTruthy();

    // Read on to a clearly different position B, and capture it.
    await turnPage(page, 4);
    const positionB = await waitForSavedChange(page, bookId!, positionA);
    expect(positionB, 'paging further should save a different position').toBeTruthy();
    expect(positionB).not.toBe(positionA);

    // Put a highlight at B, then go back and settle at A -- so the reader's
    // place and the highlight are provably different locations.
    // Text unique to this run, so the drawer row can be addressed by identity
    // rather than by position. A leftover highlight then costs nothing.
    const probeText = `preview probe ${testInfo.testId}`;
    const created = await page.request.post(`/annotations/${bookId}`, {
      data: { cfi_range: positionB, highlighted_text: probeText, highlight_color: 'yellow' },
      headers: { 'X-CSRFToken': await csrfToken(page) },
    });
    expect(created.ok(), 'the fixture highlight must exist for this test to mean anything').toBeTruthy();
    // Confirm the fixture on the SERVER before trusting any UI selector: if the
    // row is missing later, this separates "the highlight was never created"
    // from "the drawer does not show it".
    const stored = (await (await page.request.get(`/annotations/${bookId}/data.json`)).json()).annotations || [];
    expect(
      stored.filter((a: { highlighted_text?: string }) => a.highlighted_text === probeText).length,
      'this run\'s fixture highlight should be on the server exactly once',
    ).toBe(1);

    for (let i = 0; i < 4; i += 1) {
      await page.keyboard.press('ArrowLeft');
      await page.waitForTimeout(400);
    }
    const settled = await waitForSavedChange(page, bookId!, positionB);
    expect(settled, 'paging back should save again').toBeTruthy();
    expect(settled).not.toBe(positionB);

    /*
     * The whole test rests on the highlight living in a DIFFERENT spine section
     * from the reading position -- that is the difference the assertions read.
     * On a book whose chapters are long enough that paging never crossed a
     * section boundary, this spec cannot tell the two apart, and a test that
     * cannot fail is worse than no test. Skip loudly instead.
     */
    test.skip(
      spineSection(settled) === spineSection(positionB),
      'paging did not cross a section boundary in this book, so a jump would be indistinguishable',
    );

    /*
     * Reload before touching the drawer.
     *
     * The reader fetches a book's annotations once, when the book opens, so the
     * highlight created above is on the server but not in the open reader's
     * list -- the drawer would be empty and the test would fail on its own
     * fixture rather than on the behaviour. Reopening also re-enters at the
     * saved position, which is a free check that `settled` really persisted.
     */
    await page.reload();
    await page.locator('iframe').waitFor({ state: 'visible', timeout: 40_000 });
    // The baseline is whatever the reopened book settles on -- measured, not
    // assumed to equal `settled` (see waitForSavedStable).
    const baseline = await waitForSavedStable(page, bookId!);
    expect(baseline, 'the reopened book should hold a saved position').toBeTruthy();

    // THE ASSERTION. Jump to the highlight, wait well past the 800ms debounce
    // and the save round-trip, and the server must still hold where they read.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    const drawer = page.getByRole('navigation', { name: 'Highlights and notes' });
    await expect(drawer).toBeVisible();
    /*
     * Rows are addressed as list items, not by the jump button's title: the
     * button contains the highlighted text, and visible text content wins over
     * `title` when the accessible name is computed -- so the row's name is the
     * quote, never "Go to this highlight".
     */
    // Address THIS run's row by its unique text, not by position -- a leftover
    // highlight from an earlier run must not be able to redirect the click to a
    // position this test never established.
    const rows = drawer.locator('li').filter({ hasText: probeText });
    await expect(rows).toHaveCount(1);
    await rows.first().locator('button').first().click();
    await page.waitForTimeout(3_000);

    const afterJump = await savedPosition(page, bookId!);
    expect(
      spineSection(afterJump),
      'jumping to a highlight must not drag the reading position into the ' +
      'highlight\'s chapter -- on the unfixed build it does, and near the end of ' +
      'a book that also marks the whole book FINISHED',
    ).toBe(spineSection(baseline));
    expect(
      spineSection(afterJump),
      'and specifically it must not have become the highlight\'s chapter',
    ).not.toBe(spineSection(positionB));

    /*
     * The other half, and it is not optional: a fix that simply stopped saving
     * would pass everything above. Reading on from the destination IS reading,
     * so it must persist again.
     */
    await turnPage(page, 2);
    const afterReadingOn = await waitForSavedChange(page, bookId!, baseline);
    expect(
      afterReadingOn,
      'after the reader turns a page themselves, the position must save again',
    ).not.toBe(baseline);
    expect(afterReadingOn).toBeTruthy();
  });
});
