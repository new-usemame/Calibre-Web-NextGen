import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const json = async (path, options) => (await page.request.fetch(base + path, options)).json();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const readerResponses = [];
page.on('response', async response => {
  if (response.url().includes('/bookmark?')) readerResponses.push(await response.json());
});
const waitPoint = async (cfi, id) => {
  try {
    await expect.poll(() => page.evaluate(([cfi, id]) => window.pointVisible?.(cfi, id), [cfi, id]), {timeout:30000}).toBe(true);
  } catch (error) {
    console.log('Exact resume diagnostic ' + JSON.stringify({cfi, id, readerResponses,
      reader: await page.evaluate(() => ({targets: window.displayTargets,
        resolved: window.resolvedRanges.slice(0, 8), visible: window.visiblePercentageRange()}))}));
    throw error;
  }
  assert.ok(await page.evaluate(cfi => window.displayTargets.includes(cfi), cfi));
};
// Each API read remains bounded. A late conversion must become available on
// subsequent reads before exercising the real browser's exact-span contract.
const exactWire = async () => {
  let wire;
  await expect.poll(async () => {
    wire = await json('/api/v1/books/42/bookmark');
    return Boolean(wire.resume?.cfi && wire.resume?.epub_sha256);
  }, {timeout:30000, message:'Completed Kobo conversion must supply CFI and fingerprint'}).toBe(true);
  assert.match(wire.resume.epub_sha256, /^[a-f0-9]{64}$/);
  return wire;
};
try {
  const wire = await exactWire();
  assert.ok(wire.resume.cfi, JSON.stringify(wire));
  assert.equal(wire.resume.mode, 'automatic');
  assert.equal(wire.resume.percentage, 95);
  await page.goto(base + '/e2e/reader-resume/index.html');
  await waitPoint(wire.resume.cfi, 'kobo.1.50');
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, null);
  console.log('Kobo automatic: exact span kobo.1.50 is visible despite 95% carrier; no bookmark written');
  await page.getByRole('button', {name:'Next page', exact:true}).first().click();
  await expect.poll(async () => (await json('/api/v1/books/42/bookmark')).bookmark, {timeout:15000}).not.toBeNull();
  const local = (await json('/api/v1/books/42/bookmark')).bookmark;
  await json('/test-state/kobo', {method:'POST', data:{value:'kobo.1.20'}});
  const offer = await exactWire();
  assert.equal(offer.resume.mode, 'offer');
  assert.equal(offer.bookmark, local);
  await page.reload();
  const button = page.getByRole('button', {name:'Resume at 95% from another device', exact:true});
  await button.waitFor();
  assert.equal(await page.evaluate(cfi => window.displayTargets.includes(cfi), offer.resume.cfi), false);
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, local);
  await button.focus();
  await page.keyboard.press('Enter');
  await waitPoint(offer.resume.cfi, 'kobo.1.20');
  await page.waitForTimeout(1200);
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark, local);
  console.log('Kobo offer: local CFI retained until acceptance; exact kobo.1.20 visible; stored CFI unchanged');
  await json('/test-state/kobo', {method:'POST', data:{value:'kobo.999.999'}});
  const fallback = await json('/api/v1/books/42/bookmark');
  assert.deepEqual(Object.keys(fallback.resume).sort(), ['mode', 'percentage', 'synced_at']);
  await page.reload();
  await button.click();
  await expect.poll(() => page.evaluate(() => window.visiblePercentageRange()), {timeout:30000}).toEqual(expect.arrayContaining([expect.any(Number)]));
  await expect.poll(async () => {
    const [start, end] = await page.evaluate(() => window.visiblePercentageRange());
    return start <= 95 && end >= 95;
  }).toBe(true);
  console.log('Unresolvable Kobo span: original percentage-only payload and visible 95% resume');
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
