import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

async function detail(page: Page, id: number) {
  const response = await page.request.get(`/api/v1/books/${id}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function bookmark(page: Page, id: number) {
  return (await (await page.request.get(`/api/v1/books/${id}/bookmark?format=epub`)).json()).bookmark;
}
async function readableEpub(page: Page) {
  const response = await page.request.get('/api/v1/books?per_page=100&sort=new');
  for (const item of (await response.json()).items ?? []) {
    if (!item.formats.some((f: string) => f.toLowerCase() === 'epub')) continue;
    const book = await detail(page, item.id);
    const format = book.formats.find((f: { format: string; size_bytes: number }) =>
      f.format.toLowerCase() === 'epub' && f.size_bytes >= 60_000);
    if (format && (await page.request.get(format.content_url || `/show/${item.id}/epub`)).ok()) return item.id as number;
  }
  throw new Error('This reader test needs a real EPUB with enough prose to turn pages.');
}
async function readerReady(page: Page) {
  await expect(page.locator('iframe').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Next page', exact: true })).toBeVisible();
}

test('lookup survives navigation without replacing the saved place or Reading marker', async ({ secondaryUser }, testInfo) => {
  const page = secondaryUser.page;
  await page.setViewportSize(testInfo.project.name === 'mobile' ? { width: 375, height: 667 } : { width: 1280, height: 800 });
  const id = await readableEpub(page);
  // Establish a real saved position first, scoped to this test's owned account.
  await page.goto(`/app/read/${id}`);
  await readerReady(page);
  for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
  await expect.poll(() => bookmark(page, id), { timeout: 20_000 }).toBeTruthy();
  await page.goto(`/app/book/${id}`);
  await expect(page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  const saved = await bookmark(page, id);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Remove from Currently Reading', exact: true }).click();
  await expect.poll(async () => (await detail(page, id)).in_progress).toBe(false);
  expect(await bookmark(page, id)).toBe(saved);
  const state = await detail(page, id);
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/(bookmark|read|stop-reading)(?:\?|$)/.test(request.url())) writes.push(request.url());
  });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Open without saving progress', exact: true }).click();
  await expect(page).toHaveURL(/lookup=1/);
  await readerReady(page);
  await expect(page.getByText('Progress is not being saved.', { exact: true })).toBeVisible();
  for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight');
  const screenshot = testInfo.outputPath('lookup-reader.jpg');
  await page.screenshot({ path: screenshot, type: 'jpeg', quality: 75, animations: 'disabled' });
  await testInfo.attach('Lookup reader', { path: screenshot, contentType: 'image/jpeg' });
  // The regular reader's debounce is 800ms; observe beyond that interval and
  // also after unmount, when its separate keepalive save normally fires.
  await page.waitForTimeout(1800);
  await page.getByRole('link', { name: 'Close reader', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await page.waitForTimeout(300);
  expect(writes).toEqual([]);
  expect(await bookmark(page, id)).toBe(saved);
  const after = await detail(page, id);
  expect([after.read, after.in_progress, after.kosync_progress]).toEqual([state.read, state.in_progress, state.kosync_progress]);

  // Choosing ordinary Read again returns to the normal saving behavior.
  await page.getByRole('link', { name: 'Read now', exact: true }).click();
  await readerReady(page);
  await expect(page.getByText('Progress is not being saved.', { exact: true })).toHaveCount(0);
  for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight');
  await expect.poll(() => bookmark(page, id), { timeout: 20_000 }).not.toBe(saved);
});
