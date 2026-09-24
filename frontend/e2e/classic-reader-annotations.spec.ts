import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Every annotation/bookmark
// write is intercepted; the actual reader HTML, scripts, epub.js and UI run.
const builder = fileURLToPath(new URL('../../tests/fixtures/reader_archive.py', import.meta.url));
const installation = '293e761d-9fc4-4d78-a2c7-6aaeb29edc77';
const quote = 'Native reader passage for testing.';

test('classic annotation tab, exact jump, selection save and reading progress use the same browser identity', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript((installation) => {
    localStorage.setItem('cwng.webreader.installation-id.v1', installation);
    window.addEventListener('locationchange', () => {
      if ((window as any).epub?.locations?.length() > 0) (window as any).__classicPositionReady = true;
    });
  }, installation);
  const catalog = await (await page.request.get('/api/v1/books?per_page=100')).json();
  const book = catalog.items.find((item: { formats?: string[] }) =>
    item.formats?.some((format) => format.toLowerCase() === 'epub'));
  expect(book, 'the seeded library must contain a readable EPUB').toBeTruthy();
  const id = book.id;
  const bytes = execFileSync('python3', [builder], { input: JSON.stringify([
    '<p>Opening chapter without the native selection.</p>',
    '<p>' + 'Earlier introductory text. '.repeat(700) + '</p>' +
      `<p><span id="kobo.15.1">${quote}</span></p>`,
  ]) });
  const annotation = { annotation_id: 'classic-native', content_id: 'fixture!!OPS/part1/chapter.xhtml',
    start_kobospan: 'kobo.15.1', end_kobospan: 'kobo.15.1', start_offset: 0, end_offset: quote.length,
    highlighted_text: quote, highlight_color: 'yellow', source: 'kobo', cfi_range: 'FOREIGN_CFI_MUST_NOT_BE_USED' };
  await page.route(`**/show/${id}/**`, (route) => route.fulfill({ contentType: 'application/epub+zip', body: bytes }));
  await page.route(`**/annotations/${id}/data.json`, (route) => route.fulfill({ json: { annotations: [annotation], devices: {} } }));
  const saves: { body: any; installation?: string }[] = [];
  await page.route(`**/annotations/${id}`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const body = route.request().postDataJSON();
    saves.push({ body, installation: route.request().headers()['x-cwng-webreader-installation-id'] });
    await route.fulfill({ status: 201, json: { ...annotation, ...body, annotation_id: 'classic-created' } });
  });
  const bookmarks: { body: string | null; installation?: string }[] = [];
  await page.route(`**/ajax/bookmark/${id}/*`, async (route) => {
    bookmarks.push({ body: route.request().postData(), installation: route.request().headers()['x-cwng-webreader-installation-id'] });
    await route.fulfill({ json: {} });
  });
  await page.goto(`/read/${id}/epub`);
  await page.waitForFunction(() => (window as any).reader?.SidebarController && (window as any).__classicPositionReady);
  await page.locator('#slider').click();
  await page.locator('#show-Annotations').click();
  await expect(page.locator('#annotationsView')).toBeVisible();
  await expect(page.locator('#tocView')).toBeHidden();
  await page.locator('#show-Toc').click();
  await expect(page.locator('#annotationsView')).toBeHidden();
  await expect(page.locator('#tocView')).toBeVisible();
  await page.locator('#show-Annotations').click();
  await page.locator('[data-annotation-id="classic-native"]').click();
  // Requiring the mark in the viewport catches chapter-only navigation: this
  // passage deliberately follows many pages of introductory content.
  await expect(page.locator('.cwa-annotation-overlay rect').first()).toBeInViewport();
  let frame = page.mainFrame();
  for (const candidate of page.frames()) {
    if (candidate !== page.mainFrame() && await candidate.locator('[id="kobo.15.1"]').count()) { frame = candidate; break; }
  }
  expect(frame).not.toBe(page.mainFrame());
  const selected = await frame.evaluate(() => {
    const node = document.getElementById('kobo.15.1')!.firstChild!;
    const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, 6);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
    return range.toString();
  });
  await expect(page.locator('.cwa-ann-save')).toBeVisible();
  await page.locator('.cwa-ann-save').click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0]).toEqual({ installation, body: {
    start_kobospan: 'kobo.15.1', end_kobospan: 'kobo.15.1', start_offset: 0, end_offset: 6,
    chapter_filename: 'part1/chapter.xhtml', highlighted_text: selected, highlight_color: 'yellow', note_text: '',
  } });
  await expect(page.locator('[data-annotation-id="classic-created"]')).toBeAttached();
  // The actual native jump/selection has moved into the second chapter.
  await expect.poll(() => bookmarks.some((request) =>
    new URLSearchParams(request.body || '').get('bookmark')?.includes('/6/4'))).toBe(true);
  expect(bookmarks.every((request) => request.installation === installation)).toBe(true);
  expect(bookmarks.some((request) => new URLSearchParams(request.body || '').has('bookmark'))).toBe(true);
  expect(errors).toEqual([]);
});
