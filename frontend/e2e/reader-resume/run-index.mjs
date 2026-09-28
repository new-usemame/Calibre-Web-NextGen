import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';

const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const bookmark = async () => (await (await page.request.get(base + '/api/v1/books/42/bookmark')).json()).bookmark;
const nextPage = () => page.getByRole('button', {name:'Next page', exact:true}).first().click();
const showsSyncedPosition = () => expect.poll(() => page.evaluate(() => {
  const [start, end] = window.visiblePercentageRange();
  return start <= 95 && end >= 95;
})).toBe(true);
try {
  for (const turned of [false, true]) {
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
