import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const json = async (path, options) => (await page.request.fetch(base + path, options)).json();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const waitPercentage = async (mode, wire) => {
  try {
    await expect.poll(() => page.evaluate(() => {
      const [start, end] = window.visiblePercentageRange?.() ?? [];
      return start <= 95 && end >= 95;
    }), {timeout:30000}).toBe(true);
    if (wire.resume.cfi) {
      assert.equal(await page.evaluate(cfi => window.displayTargets.includes(cfi), wire.resume.cfi), false);
      assert.ok(await page.evaluate(cfi => window.resolvedRanges.some(
        item => item.cfi === cfi && item.result === 'null'), wire.resume.cfi));
    }
  } finally {
    console.log(JSON.stringify({mode, wire, errors, reader: await page.evaluate(() => ({
      targets: window.displayTargets, resolved: window.resolvedRanges, visible: window.visiblePercentageRange?.(),
    }))}));
  }
};
try {
  const wire = await json('/api/v1/books/42/bookmark');
  assert.equal(wire.resume.mode, 'automatic');
  assert.equal(wire.resume.percentage, 95);
  await page.goto(base + '/e2e/reader-resume/index.html');
  await waitPercentage('automatic fallback', wire);
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, null);
  await page.getByRole('button', {name:'Previous page', exact:true}).first().click();
  await expect.poll(async () => (await json('/api/v1/books/42/bookmark')).bookmark, {timeout:15000}).not.toBeNull();
  const local = (await json('/api/v1/books/42/bookmark')).bookmark;
  await json('/test-state/kobo', {method:'POST', data:{value:'kobo.1.20'}});
  const offer = await json('/api/v1/books/42/bookmark');
  assert.equal(offer.resume.mode, 'offer');
  await page.reload();
  const button = page.getByRole('button', {name:'Resume at 95% from another device', exact:true});
  await button.waitFor();
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, local);
  await button.focus();
  await page.keyboard.press('Enter');
  await waitPercentage('offer fallback', offer);
  await page.waitForTimeout(1200);
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, local);
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
