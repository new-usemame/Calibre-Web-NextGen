import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * #2253: the reader's table of contents must show every level of the book's
 * outline, and a nested entry must open the place it names.
 *
 * epub.js parses a nav document or an NCX into a tree (a Part's Chapters sit in
 * its `subitems`), and the reader kept only the top level, so a multi-level
 * book could be navigated by Part alone. A nested entry also usually names a
 * place INSIDE a chapter (ch1.xhtml#late-section); opening its chapter at the
 * first page is not opening the entry.
 *
 * The fixture is three levels deep in both TOC formats epub.js reads:
 *   Part One > Chapter One > Late Section (a fragment several pages in)
 *            > Chapter Two
 *   Afterword
 * plus a third layout with the nav document outside the package folder
 * (`../nav.xhtml` beside `OEBPS/content.opf`), where a TOC href is not relative
 * to the package document the spine is keyed by and choosing any entry did
 * nothing at all.
 */

const FIXTURES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/sample_books');

/** The text of the section epub.js currently has loaded. */
async function sectionText(page: Page): Promise<string> {
  return page.evaluate(
    () => (document.querySelector('iframe') as HTMLIFrameElement | null)?.contentDocument?.body?.innerText ?? '',
  );
}

/**
 * Whether the element is on the page the reader can see. Paginated flow lays a
 * whole section out in columns and scrolls the container sideways, so an
 * element can be in the loaded section yet several pages away.
 */
async function onVisiblePage(page: Page, id: string): Promise<boolean> {
  return page.evaluate((elementId) => {
    const frame = document.querySelector('iframe') as HTMLIFrameElement | null;
    const el = frame?.contentDocument?.getElementById(elementId);
    if (!frame || !el) return false;
    const viewport = (frame.closest('.epub-container') ?? frame.parentElement ?? frame).getBoundingClientRect();
    const box = el.getBoundingClientRect();
    const x = frame.getBoundingClientRect().left + box.left + box.width / 2;
    return x >= viewport.left && x < viewport.right;
  }, id);
}

async function openFixture(page: Page, fixture: string): Promise<void> {
  const res = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  const list = (await res.json()) as { items?: { id: number; formats?: string[] }[] };
  const book = (list.items || []).find((b) => (b.formats || []).some((f) => f.toLowerCase() === 'epub'));
  if (!book) throw new Error('no epub book in the library to host the fixture');
  await page.route('**/show/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/epub+zip', path: path.join(FIXTURES, fixture) }),
  );
  await page.goto(`/app/read/${book.id}`);
  await expect(page.locator('iframe')).toBeAttached({ timeout: 30_000 });
}

async function pick(page: Page, entry: string): Promise<void> {
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  await page.getByRole('navigation', { name: 'Table of contents' }).getByRole('button', { name: entry, exact: true }).click();
}

const LAYOUTS = [
  ['EPUB 3 nav', 'test_nested_toc.epub'],
  ['EPUB 2 NCX', 'test_nested_toc_ncx.epub'],
  ['nav outside the package folder', 'test_nested_toc_nav_outside.epub'],
];

for (const [format, fixture] of LAYOUTS) {
  test(`every level of a ${format} outline is listed, indented under its parent`, async ({ page }) => {
    await openFixture(page, fixture);
    await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
    const drawer = page.getByRole('navigation', { name: 'Table of contents' });

    const entries = ['Part One', 'Chapter One', 'Late Section', 'Chapter Two', 'Afterword'];
    for (const entry of entries) await expect(drawer.getByRole('button', { name: entry, exact: true })).toBeVisible();

    // The drawer slides in; read every entry's inset from the drawer's edge in
    // one frame once it has stopped moving.
    const inset = await drawer.evaluate(async (nav, names) => {
      await Promise.all(nav.getAnimations({ subtree: true }).map((a) => a.finished));
      const edge = nav.getBoundingClientRect().left;
      const buttons = Array.from(nav.querySelectorAll('button'));
      return Object.fromEntries(names.map((name) => {
        const button = buttons.find((b) => b.textContent?.trim() === name);
        return [name, button ? Math.round(button.getBoundingClientRect().left - edge) : NaN];
      }));
    }, entries);

    // Siblings share a level; each level sits further in than its parent.
    expect(inset['Chapter Two']).toBe(inset['Chapter One']);
    expect(inset['Afterword']).toBe(inset['Part One']);
    expect(inset['Chapter One']).toBeGreaterThan(inset['Part One']);
    expect(inset['Late Section']).toBeGreaterThan(inset['Chapter One']);
  });

  test(`a nested entry in a ${format} book opens the place it names, not its chapter's first page`, async ({ page }) => {
    await openFixture(page, fixture);

    // Control: the chapter opens at its first page, where the late section is NOT
    // visible — otherwise the last assertion could not tell the two apart.
    await pick(page, 'Chapter One');
    await expect.poll(() => onVisiblePage(page, 'chapter-one'), { timeout: 20_000 }).toBe(true);
    expect(await onVisiblePage(page, 'late-section')).toBe(false);

    // A nested entry in its own document.
    await pick(page, 'Chapter Two');
    await expect.poll(() => sectionText(page), { timeout: 20_000 }).toContain('CHAPTER-TWO-OPENING');

    // From another section, as a reader jumping around the outline would.
    await pick(page, 'Late Section');
    await expect.poll(() => onVisiblePage(page, 'late-section'), { timeout: 20_000 }).toBe(true);
    // Still there once the section's typography has settled.
    await page.waitForTimeout(1_000);
    expect(await onVisiblePage(page, 'late-section')).toBe(true);
  });
}
