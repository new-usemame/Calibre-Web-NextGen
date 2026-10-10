import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';

const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const bookmark = async () => (await (await page.request.get(base + '/api/v1/books/42/bookmark')).json()).bookmark;
const nextPage = () => page.getByRole('button', {name:'Next page', exact:true}).first().click();
/*
 * Select a run of prose the way reader-notes.spec.ts does: build the Range in
 * the book frame and dispatch the events epub.js listens for, so the real
 * `rendition.on('selected')` path opens the highlight popover.
 */
const selectText = () => page.frames().find(f => f !== page.mainFrame()).evaluate(() => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node = null;
  while ((node = walker.nextNode())) if ((node.textContent || '').trim().length > 5) break;
  const range = document.createRange();
  range.setStart(node, 0);
  range.setEnd(node, Math.min(12, node.textContent.length));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new Event('selectionchange', {bubbles:true}));
  const box = range.getBoundingClientRect();
  for (const type of ['mousedown', 'mouseup'])
    document.dispatchEvent(new MouseEvent(type, {bubbles:true, clientX:box.x + 2, clientY:box.y + 2}));
  return String(selection);
});
const showsSyncedPosition = () => expect.poll(() => page.evaluate(() => {
  const [start, end] = window.visiblePercentageRange();
  return start <= 95 && end >= 95;
})).toBe(true);
try {
  // 'turned' last: it ends by reading on, which saves a web bookmark and so
  // turns every later open into an offer rather than an automatic resume.
  for (const early of ['none', 'selected', 'selected-racing', 'turned']) {
    const turned = early === 'turned';
    await page.goto(base + '/e2e/reader-resume/index.html?holdLocations');
    // The index is still pending: the real rendition must already show text.
    await expect(page.frameLocator('iframe').locator('body')).toContainText('Paragraph 1.');
    assert.deepEqual(await page.evaluate(() => window.displayTargets), [undefined]);
    if (turned) {
      // #2358: the start page is only a placeholder while the synced position
      // is being located, so turning it is not choosing to read from there.
      await nextPage();
      await page.waitForTimeout(1200); // Past the 800ms persistence debounce.
      assert.equal(await bookmark(), null,
        'a page turn before the automatic jump must not replace the synced position');
    }
    if (early === 'selected-racing') {
      // The CI timing (run 36743406022): the index finishes inside epub.js's
      // 250ms selection debounce, so the jump is decided BEFORE 'selected'
      // fires. The live selection itself must hold the page.
      assert.notEqual(await selectText(), '');
      await page.evaluate(() => window.releaseLocations());
      await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length)).toBe(1);
      await expect(page.getByRole('button', {name:'Add note'})).toBeVisible();
      await expect(page.getByRole('button', {name:/^Resume at \d+% from another device$/})).toBeVisible();
      assert.deepEqual(await page.evaluate(() => window.displayTargets), [undefined],
        'a selection made just before the index lands must keep the reader on its page');
      assert.equal(await bookmark(), null);
      console.log('Pending index: a selection racing the index kept its page and popover; the synced position became an offer');
      continue;
    }
    if (early === 'selected') {
      // Selecting text on the placeholder start is the reader acting on THIS
      // page -- the first half of making a highlight, which already wins over
      // the pending jump. The late jump used to land anyway and re-render the
      // view under the open popover, so the selection and its "Add note"
      // button vanished mid-gesture (CI main, 2026-09-30, reader-notes mobile).
      assert.notEqual(await selectText(), '');
      await expect(page.getByRole('button', {name:'Add note'})).toBeVisible();
      await page.evaluate(() => window.releaseLocations());
      await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length)).toBe(1);
      await expect(page.getByRole('button', {name:/^Resume at \d+% from another device$/})).toBeVisible();
      assert.deepEqual(await page.evaluate(() => window.displayTargets), [undefined],
        'a selection before the automatic jump must keep the reader on its page');
      await expect(page.getByRole('button', {name:'Add note'})).toBeVisible();
      assert.equal(await bookmark(), null);
      console.log('Pending index: a selection on the placeholder kept its page and popover; the synced position became an offer');
      continue;
    }
    await page.evaluate(() => window.releaseLocations());
    await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length)).toBe(1);
    await showsSyncedPosition();
    assert.equal(await bookmark(), null);
    if (turned) {
      // Reading on from the synced position is the reader's own choice again.
      await nextPage();
      await expect.poll(bookmark, {timeout:15000}).not.toBeNull();
      assert.ok(Number(await page.getByRole('progressbar').getAttribute('aria-valuenow')) >= 90);
    }
    console.log(`Pending index: first display reached; late percentage applied without saving${turned ? ' despite an early page turn, which did not save' : ''}`);
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
