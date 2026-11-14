import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';

/*
 * #2783: a place saved before the locations index exists has no percentage,
 * because epub.js cannot compute one yet. The percentage is the part the
 * server shares with the user's other devices (a Kobo, KOReader), so a reader
 * who turns a page in the first seconds and then puts the book down would
 * never share where they are. Once the index lands, that same place must be
 * saved again with its percentage.
 */
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base = `http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const saves = [];
page.on('request', request => {
  if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/books/42/bookmark')
    saves.push(request.postDataJSON());
});
const bookmark = async () => (await (await page.request.get(base + '/api/v1/books/42/bookmark')).json()).bookmark;
const nextPage = () => page.getByRole('button', {name:'Next page', exact:true}).first().click();
// The first page is the cover, so wait for the book frame rather than for text.
const rendered = () => expect(page.frameLocator('iframe').locator('body')).toBeAttached({timeout:15000});
try {
  // Leave a web bookmark first, so the next open resumes there with nothing
  // pending and displays without waiting for the index.
  await page.goto(base + '/e2e/reader-resume/index.html');
  await rendered();
  await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length), {timeout:15000}).toBe(1);
  await nextPage();
  await expect.poll(bookmark, {timeout:15000}).not.toBeNull();
  const firstPlace = await bookmark();

  saves.length = 0;
  await page.goto(base + '/e2e/reader-resume/index.html?holdLocations');
  await rendered();
  assert.equal(await page.evaluate(() => window.locationGenerationMs.length), 0, 'the index is still held');
  await nextPage();
  await expect.poll(() => saves.length, {timeout:15000}).toBeGreaterThan(0);
  const early = saves.at(-1);
  assert.notEqual(early.bookmark, firstPlace, 'the page turn moved the place');
  assert.equal(early.percentage, undefined, 'no percentage can exist before the index');
  await expect.poll(bookmark, {timeout:15000}).toBe(early.bookmark);

  await page.evaluate(() => window.releaseLocations());
  await expect.poll(() => page.evaluate(() => window.locationGenerationMs.length), {timeout:15000}).toBe(1);
  await expect.poll(() => saves.some(save => save.bookmark === early.bookmark
    && typeof save.percentage === 'number' && save.percentage > 0), {timeout:15000}).toBe(true);
  assert.equal(await bookmark(), early.bookmark, 'the late save is the same place, not a new one');
  console.log('Pending index: a place saved before the index was saved again with its percentage once the index landed');
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
