import { expect, test } from './fixtures';
import type { Page, Response } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EPUB_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/test_noteref_links.epub',
);
const REGULAR_FONT_PATH = '/static/fonts/literata/Literata-Regular.woff2';

test.describe.configure({ mode: 'serial' });

async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  return ((await response.json()) as { csrf_token: string }).csrf_token;
}

async function firstEpubId(page: Page): Promise<number> {
  const response = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  const body = (await response.json()) as { items?: { id: number; formats?: string[] }[] };
  const book = body.items?.find((item) => item.formats?.some((format) => format.toLowerCase() === 'epub'));
  if (!book) throw new Error('the owned test account must be able to read a library EPUB');
  return book.id;
}

async function openFixture(page: Page, id: number, classic: boolean): Promise<void> {
  await page.route('**/show/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/epub+zip', path: EPUB_FIXTURE }),
  );
  await page.goto(classic ? `/read/${id}/epub` : `/app/read/${id}`);
  await expect(page.locator('iframe').first()).toBeAttached({ timeout: 30_000 });
  await expect.poll(async () => page.locator('iframe').first().evaluate((element) => {
    const doc = (element as HTMLIFrameElement).contentDocument;
    return doc?.body?.innerText ?? '';
  }), { timeout: 30_000 }).toContain('NOTEREF-SECTION-1');
}

async function renderedLiterata(page: Page): Promise<{ family: string; loaded: boolean }> {
  return page.locator('iframe').first().evaluate((element) => {
    const doc = (element as HTMLIFrameElement).contentDocument!;
    const paragraph = doc.querySelector('p')!;
    const regularFaces = Array.from(doc.fonts).filter((face) =>
      face.family.replace(/[\"']/g, '') === 'Literata' && face.weight === 'normal' && face.style === 'normal',
    );
    return {
      family: doc.defaultView!.getComputedStyle(paragraph).fontFamily,
      loaded: regularFaces.some((face) => face.status === 'loaded'),
    };
  });
}

async function assertFontRequest(responsePromise: Promise<Response>) {
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toMatch(/font|octet-stream/i);
  const bytes = await response.body();
  expect(bytes.subarray(0, 4).toString()).toBe('wOF2');
  const bundled = await readFile(path.resolve(path.dirname(fileURLToPath(import.meta.url)),
    '../../cps/static/fonts/literata/Literata-Regular.woff2'));
  const deliveredSha256 = createHash('sha256').update(bytes).digest('hex');
  const bundledSha256 = createHash('sha256').update(bundled).digest('hex');
  expect(deliveredSha256).toBe(bundledSha256);
  await test.info().attach('literata-regular-http-proof.json', {
    body: JSON.stringify({
      url: response.url(),
      status: response.status(),
      contentType: response.headers()['content-type'],
      byteLength: bytes.byteLength,
      woff2Signature: bytes.subarray(0, 4).toString(),
      deliveredSha256,
      bundledSha256,
    }, null, 2),
    contentType: 'application/json',
  });
}

let savedFont: unknown;

test.beforeEach(async ({ secondaryUser }) => {
  const response = await secondaryUser.page.request.get('/api/v1/reader/settings');
  savedFont = ((await response.json()) as { reader: { font?: unknown } }).reader.font;
});

test.afterEach(async ({ secondaryUser }) => {
  if (savedFont === undefined) return;
  await secondaryUser.page.request.post('/api/v1/reader/settings', {
    headers: { 'X-CSRFToken': await csrfToken(secondaryUser.page) },
    data: { font: savedFont },
  });
});

test('SPA applies Literata in the EPUB document and serves the bundled font', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  const id = await firstEpubId(page);
  await openFixture(page, id, false);

  const fontResponse = page.waitForResponse((response) =>
    response.url().includes(REGULAR_FONT_PATH) && response.status() === 200,
  );
  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await page.getByLabel('Font family').selectOption('Literata');
  await assertFontRequest(fontResponse);

  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/reader/settings');
    return ((await response.json()) as { reader: { font: string } }).reader.font;
  }).toBe('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);
});

test('classic EPUB reader applies and persists Literata using the served font', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  const id = await firstEpubId(page);
  await openFixture(page, id, true);

  const fontResponse = page.waitForResponse((response) =>
    response.url().includes(REGULAR_FONT_PATH) && response.status() === 200,
  );
  await page.locator('#setting').click();
  await page.locator('#Literata').click();
  await assertFontRequest(fontResponse);

  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/reader/settings');
    return ((await response.json()) as { reader: { font: string } }).reader.font;
  }).toBe('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);
});
