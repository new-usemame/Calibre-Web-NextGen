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

/** Advance through the real reader until the marker is on the visible page. */
async function markerOnPage(page: Page, href: string) {
  const target = page.locator(`[data-testid="reader-link-hit"][data-href="${href}"]`).first();
  const geometry = () => page.evaluate(href => {
    const frame = document.querySelector('iframe');
    const anchor = Array.from(frame?.contentDocument?.querySelectorAll('a[href]') || [])
      .find(node => node.getAttribute('href') === href);
    if (!frame || !anchor) return null;
    const view = frame.parentElement?.closest('[class*="viewer_"]');
    if (!view) throw new Error('reader viewer missing');
    const bounds = view.getBoundingClientRect(), origin = frame.getBoundingClientRect();
    const rect = anchor.getBoundingClientRect();
    const left = origin.left + rect.left, top = origin.top + rect.top;
    return {left, top, visible: rect.width > 0 && rect.height > 0 &&
      left + rect.width > Math.max(0, bounds.left) && left < Math.min(innerWidth, bounds.right) &&
      top + rect.height > Math.max(0, bounds.top) && top < Math.min(innerHeight, bounds.bottom)};
  }, href);
  for (let turns = 0; turns < 10; turns++) {
    const before = await geometry();
    if (before?.visible) {
      await expect(target).toBeInViewport();
      return target;
    }
    await page.getByRole('button', {name: 'Next page', exact: true}).click();
    await expect.poll(geometry, {message: 'the reader advances to the next page'}).not.toEqual(before);
  }
  throw new Error(`note marker ${href} was not visible within ten fixture pages`);
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
  // Enlarged saved text makes revealing this long note add pagination columns.
  // Keep the preference deterministic without changing the shared account.
  await page.route('**/api/v1/reader/settings', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    const payload = await response.json();
    payload.reader = {...payload.reader, fontSize: 130, lineHeight: 190, margin: 32, font: 'Arial'};
    await route.fulfill({response, json: payload});
  });
  await openBook(page);
  const chapter = page.frameLocator('iframe').first();
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', '0px');
  const marker = await markerOnPage(page, '#fn-80-2');
  if (test.info().project.use.hasTouch) await marker.tap(); else await marker.click();
  const note = page.getByRole('dialog', {name: 'Note', exact: true});
  await expect(note).toContainText('NOTE-EPUB-TEXT-ALPHA');
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', '0px');
  expect(await page.evaluate(() => {
    const state = window as unknown as Record<string, unknown>;
    return !!(state.NOTE_SCRIPT_RAN || state.NOTE_IMG_ONERROR_RAN || state.NOTE_HANDLER_RAN);
  })).toBe(false);
  const frame = page.locator('iframe').first();
  const hiddenWidth = await frame.evaluate(node => node.getBoundingClientRect().width);
  await note.getByRole('button', {name: 'Go to note', exact: true}).click();
  await expect(note).toBeHidden();
  await expect(chapter.locator('#fn-80-2')).toHaveCSS('max-height', 'none');
  // A brief intersection before the revealed chapter expands is not arrival.
  await expect.poll(() => frame.evaluate(node => node.getBoundingClientRect().width),
    {message: 'the revealed long note has expanded the chapter'}).toBeGreaterThan(hiddenWidth);
  // A multi-column aside has one union box spanning offscreen columns. Check
  // the first note paragraph the reader must actually see, including clipping.
  await expect(chapter.locator('#fn-80-2 > p').first()).toBeInViewport();
  expect(await page.evaluate(() => !!(window as unknown as Record<string, unknown>).NOTE_SCRIPT_RAN)).toBe(false);
});

test('#2255 a cross-chapter hidden note reveals in its own frame', async ({page}) => {
  await openBook(page);
  const marker = await markerOnPage(page, 'ch2.xhtml#fn-cross');
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
  const marker = await markerOnPage(page, '#note%2520id');
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
  const marker = await markerOnPage(page, '#fn-80-2');
  await marker.focus();
  await expect(marker).toBeFocused();
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


test('#2255 later-column links are excluded until their page is visible with enlarged text', async ({page}) => {
  // Reproduce the saved CI preference without changing the shared account.
  await page.route('**/api/v1/reader/settings', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    const payload = await response.json();
    payload.reader = {...payload.reader, fontSize: 130, lineHeight: 190, margin: 32, font: 'Arial'};
    await route.fulfill({response, json: payload});
  });
  await openBook(page);
  const marker = await markerOnPage(page, '#fn-80-2');
  await expect.poll(() => page.locator('[data-testid="reader-link-hit"]').evaluateAll(nodes =>
    nodes.every(node => {
      const rect = node.getBoundingClientRect();
      const frame = document.querySelector('iframe');
      const bounds = frame?.parentElement?.closest('[class*="viewer_"]')?.getBoundingClientRect();
      return !!bounds && rect.left >= Math.max(0, bounds.left) && rect.right <= Math.min(innerWidth, bounds.right) &&
        rect.top >= Math.max(0, bounds.top) && rect.bottom <= Math.min(innerHeight, bounds.bottom);
    })), {message: 'only visible page links enter the focus order'}).toBe(true);
  if (test.info().project.use.hasTouch) await marker.tap(); else await marker.click();
  await expect(page.getByRole('dialog', {name: 'Note', exact: true})).toContainText('NOTE-EPUB-TEXT-ALPHA');
});
