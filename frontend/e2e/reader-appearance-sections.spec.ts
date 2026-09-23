import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * #2254: a reading-appearance change must survive the next chapter.
 *
 * epub.js renders every spine section into a fresh iframe and emits 'rendered',
 * and the reader re-applies theme and typography from that listener. The
 * listener is attached once, when the book opens, so it closed over the
 * settings of that moment: a larger font the reader chose while reading was
 * put back to the opening size as soon as the next section rendered, while the
 * panel still showed the new value.
 *
 * The two-section fixture makes the boundary exact: set the appearance in
 * section 1, cross into section 2 through the reader's own table of contents,
 * and measure the text epub.js actually laid out there.
 */

const EPUB_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/test_noteref_links.epub',
);

// Reader.tsx THEMES: the page ground each reader theme paints.
const SEPIA_GROUND = 'rgb(242, 230, 207)';
const DARK_GROUND = 'rgb(21, 17, 12)';

test.describe.configure({ mode: 'serial' });

type Appearance = { fontPx: number; lineHeight: string; ground: string; text: string };

async function csrfToken(page: Page): Promise<string> {
  return page.request
    .get('/api/v1/auth/csrf')
    .then((r) => r.json())
    .then((b: { csrf_token: string }) => b.csrf_token);
}

/** What the reader's current section actually renders: its first paragraph and page ground. */
async function sectionAppearance(page: Page): Promise<Appearance | null> {
  return page.evaluate(() => {
    const doc = (document.querySelector('iframe') as HTMLIFrameElement | null)?.contentDocument;
    const para = doc?.querySelector('p');
    if (!doc?.body || !para) return null;
    const view = doc.defaultView!;
    return {
      fontPx: parseFloat(view.getComputedStyle(para).fontSize),
      lineHeight: doc.body.style.getPropertyValue('line-height'),
      ground: view.getComputedStyle(doc.body).backgroundColor,
      text: doc.body.innerText,
    };
  });
}

async function openFixtureAtFirstSection(page: Page): Promise<void> {
  const res = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  const list = (await res.json()) as { items?: { id: number; formats?: string[] }[] };
  const book = (list.items || []).find((b) => (b.formats || []).some((f) => f.toLowerCase() === 'epub'));
  if (!book) throw new Error('no epub book in the library to host the fixture');
  await page.route('**/show/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/epub+zip', path: EPUB_FIXTURE }),
  );
  await page.goto(`/app/read/${book.id}`);
  await expect(page.locator('iframe')).toBeAttached({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  await page.getByRole('button', { name: 'Noteref Chapter', exact: true }).click();
  await expect.poll(async () => (await sectionAppearance(page))?.text ?? '', { timeout: 20_000 })
    .toContain('NOTEREF-SECTION-1');
}

let savedReader: Record<string, unknown> | null = null;

test.beforeEach(async ({ page }) => {
  await page.goto('/app');
  savedReader = (await (await page.request.get('/api/v1/reader/settings')).json()).reader;
});

test.afterEach(async ({ page }) => {
  // Reader appearance is per user and server-side; hand the next spec back the account it had.
  if (!savedReader) return;
  await page.request.post('/api/v1/reader/settings', {
    headers: { 'X-CSRFToken': await csrfToken(page), 'Content-Type': 'application/json' },
    data: savedReader,
  });
});

test('font size, line height and theme chosen mid-book still apply in the next section', async ({ page }) => {
  await openFixtureAtFirstSection(page);

  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await expect(page.getByRole('dialog', { name: 'Reading appearance' })).toBeVisible();
  // A reference size, measured on this engine rather than assumed.
  await page.getByLabel('Font size').fill('100');
  await expect.poll(async () => (await sectionAppearance(page))?.fontPx).toBeGreaterThan(0);
  const basePx = (await sectionAppearance(page))!.fontPx;

  // Pick whichever theme the account is NOT on, so the change is observable.
  const onSepia = (await sectionAppearance(page))!.ground === SEPIA_GROUND;
  const [themeName, ground] = onSepia ? ['Dark', DARK_GROUND] : ['Sepia', SEPIA_GROUND];
  await page.getByRole('button', { name: themeName, exact: true }).click();
  await page.getByLabel('Font size').fill('150');
  await page.getByLabel('Line height').fill('200');

  // The page on screen takes the change at once.
  await expect.poll(() => sectionAppearance(page)).toMatchObject({
    fontPx: basePx * 1.5, lineHeight: '2', ground,
  });

  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  await page.getByRole('button', { name: 'Target Chapter', exact: true }).click();
  await expect.poll(async () => (await sectionAppearance(page))?.text ?? '', { timeout: 20_000 })
    .toContain('NOTEREF-SECTION-2');

  /*
   * The regression re-applied the stale values from the section's 'rendered'
   * listener, which runs after the new text is already on the page. Let that
   * settle, then require the chosen appearance, not merely a moment of it.
   */
  await page.waitForTimeout(1_500);
  expect(await sectionAppearance(page)).toMatchObject({ fontPx: basePx * 1.5, lineHeight: '2', ground });

  // And the panel agrees with the page.
  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await expect(page.getByLabel('Font size')).toHaveValue('150');
});
