import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';

const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  for (const moved of [false, true]) {
    await page.goto(base + '/e2e/reader-resume/index.html?holdLocations');
    // The index is still pending: the real rendition must already show text.
    await expect(page.frameLocator('iframe').locator('body')).toContainText('Paragraph 1.');
    assert.deepEqual(await page.evaluate(() => window.displayTargets), [undefined]);
    if (moved) {
      await page.getByRole('button', {name:'Next page', exact:true}).first().click();
      await expect.poll(async () => (await (await page.request.get(
        base + '/api/v1/books/42/bookmark')).json()).bookmark).not.toBeNull();
    }
    const targets = await page.evaluate(() => window.displayTargets);
    await page.evaluate(() => window.releaseLocations());
    await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length)).toBe(1);
    if (moved) {
      assert.deepEqual(await page.evaluate(() => window.displayTargets), targets,
        'a late index must not move someone who has started reading');
    } else {
      await expect.poll(() => page.evaluate(() => {
        const [start, end] = window.visiblePercentageRange();
        return start <= 95 && end >= 95;
      })).toBe(true);
      assert.equal((await (await page.request.get(base + '/api/v1/books/42/bookmark')).json()).bookmark, null);
    }
    console.log(`Pending index: first display reached; late percentage ${moved ? 'suppressed after page turn' : 'applied without saving'}`);
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
