import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const fixture = path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/test_namespace_notes.epub');

async function openBook(page: Page) {
  const response = await page.request.get('/api/v1/books?per_page=200');
  expect(response.ok()).toBeTruthy();
  const book = (await response.json()).items.find((item: {formats: string[]}) =>
    item.formats.some(format => format.toLowerCase() === 'epub'));
  expect(book, 'an accessible seeded EPUB').toBeTruthy();
  await page.route('**/show/**', route => route.fulfill({
    status: 200, contentType: 'application/epub+zip', path: fixture,
  }));
  // Lookup mode exercises the reader without changing shared read/bookmark state.
  await page.goto(`/app/read/${book.id}?lookup=1`);
  await expect(page.locator('iframe').first()).toBeAttached();
  await page.getByRole('button', {name: 'Table of contents', exact: true}).click();
  await page.getByRole('button', {name: 'Noteref Chapter', exact: true}).click();
  await expect(page.frameLocator('iframe').first().locator('#semantic')).toBeVisible();
}

test('#2255 publisher namespaces apply to external and inline CSS, including non-footnote rules', async ({page}) => {
  await openBook(page);
  const chapter = page.frameLocator('iframe').first();
  const semantic = chapter.locator('#semantic');
  await expect(semantic).toHaveCSS('border-right-width', '3px');
  await expect(semantic).toHaveCSS('border-left-width', '7px');
  await expect(semantic).toHaveCSS('border-bottom-width', '5px');
  await expect(semantic).toHaveCSS('border-top-width', '2px');
  await expect(chapter.locator('#shadowed')).toHaveCSS('border-left-width', '11px');
  await expect(chapter.locator('#sibling')).toHaveCSS('border-left-width', '7px');
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', '0px');
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('overflow', 'hidden');
  expect(await chapter.locator('#fn-80-2').evaluate(node => node.getBoundingClientRect().height)).toBe(0);
  await page.screenshot({path: test.info().outputPath('publisher-namespace-css.jpg'), type: 'jpeg', quality: 65});
});

test('#2255 a hidden publisher footnote opens safely and Go to note reveals its target', async ({page}) => {
  await openBook(page);
  const chapter = page.frameLocator('iframe').first();
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', '0px');
  const marker = page.locator('[data-testid="reader-link-hit"][data-href="#fn-80-2"]').first();
  if (test.info().project.use.hasTouch) await marker.tap(); else await marker.click();
  const note = page.getByRole('dialog', {name: 'Note', exact: true});
  await expect(note).toContainText('NOTE-EPUB-TEXT-ALPHA');
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', '0px');
  expect(await page.evaluate(() => {
    const state = window as unknown as Record<string, unknown>;
    return !!(state.NOTE_SCRIPT_RAN || state.NOTE_IMG_ONERROR_RAN || state.NOTE_HANDLER_RAN);
  })).toBe(false);
  await note.getByRole('button', {name: 'Go to note', exact: true}).click();
  await expect(note).toBeHidden();
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', 'none');
  // A multi-column aside has one union box spanning offscreen columns. Check
  // the first note paragraph the reader must actually see, including clipping.
  await expect(chapter.locator('#fn-80-2 > p').first()).toBeInViewport();
  expect(await page.evaluate(() => !!(window as unknown as Record<string, unknown>).NOTE_SCRIPT_RAN)).toBe(false);
});

test('#2255 a cross-chapter hidden note reveals in its own frame', async ({page}) => {
  await openBook(page);
  const marker = page.locator('[data-testid="reader-link-hit"][data-href="ch2.xhtml#fn-cross"]').first();
  if (test.info().project.use.hasTouch) await marker.tap(); else await marker.click();
  const note = page.getByRole('dialog', {name: 'Note', exact: true});
  await expect(note).toContainText('NOTE-CROSS-TEXT-GAMMA');
  await expect(page.frameLocator('iframe').first().locator('#semantic')).toBeVisible();
  await note.getByRole('button', {name: 'Go to note', exact: true}).click();
  await expect(note).toBeHidden();
  const target = page.frameLocator('iframe').first().locator('#fn-cross');
  await expect(target).toHaveCSS('max-height', 'none');
  await expect(target).toBeInViewport();
});

test('#2255 percent escapes in a literal note ID do not target another ID', async ({page}) => {
  await openBook(page);
  const marker = page.locator('[data-testid="reader-link-hit"][data-href="#note%2520id"]').first();
  if (test.info().project.use.hasTouch) await marker.tap(); else await marker.click();
  const note = page.getByRole('dialog', {name: 'Note', exact: true});
  await expect(note).toContainText('LITERAL-PERCENT-NOTE');
  await note.getByRole('button', {name: 'Go to note', exact: true}).click();
  await expect(note).toBeHidden();
  const target = page.frameLocator('iframe').first().locator('[id="note%20id"]');
  await expect(target).toHaveCSS('max-height', 'none');
  await expect(target).toBeInViewport();
});


test('#2255 a long note is keyboard scrollable and Escape returns to its marker', async ({page}) => {
  await openBook(page);
  const marker = page.locator('[data-testid="reader-link-hit"][data-href="#fn-80-2"]').first();
  await marker.focus();
  await page.keyboard.press('Enter');
  const note = page.getByRole('dialog', {name: 'Note', exact: true});
  await expect(note).toBeVisible();
  const audit = await new AxeBuilder({page}).include('[role="dialog"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(audit.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  const body = note.getByRole('region', {name: 'Note', exact: true});
  await note.getByRole('button', {name: 'Close', exact: true}).focus();
  await page.keyboard.press('Tab');
  await expect(body).toBeFocused();
  expect(await body.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  const start = await body.evaluate(node => node.scrollTop);
  await page.keyboard.press('PageDown');
  await expect.poll(() => body.evaluate(node => node.scrollTop)).toBeGreaterThan(start);
  await page.keyboard.press('Escape');
  await expect(note).toBeHidden();
  await expect(marker).toBeFocused();
});
