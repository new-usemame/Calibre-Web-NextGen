import { expect, test } from './fixtures';
import type { BrowserContext, Page, Response, TestInfo } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cleanupOwnedUser, createOwnedUser, createOwnedUserIdentity } from './user-reaper';
import { adminCredentialsFromEnvironment, DirectAdminApi } from './direct-admin-api';

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

async function savedReaderFont(page: Page): Promise<string | undefined> {
  const response = await page.request.get('/api/v1/reader/settings');
  const body = await response.json() as { reader?: { font?: string } };
  if (!response.ok() || !body.reader) {
    throw new Error(`reader-settings GET returned ${response.status()}: ${JSON.stringify(body)}`);
  }
  return body.reader.font;
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
  const proof = JSON.stringify({
      url: response.url(),
      status: response.status(),
      contentType: response.headers()['content-type'],
      byteLength: bytes.byteLength,
      woff2Signature: bytes.subarray(0, 4).toString(),
      deliveredSha256,
      bundledSha256,
    }, null, 2);
  await writeFile(test.info().outputPath('literata-regular-http-proof.json'), proof);
  await test.info().attach('literata-regular-http-proof.json', {
    body: proof,
    contentType: 'application/json',
  });
}

let savedFont: unknown;
let readerContext: BrowserContext | undefined;
let activePage: Page | undefined;
let nativeUserAgent = '';
let adminApi: DirectAdminApi | undefined;
let ownedUser: Awaited<ReturnType<typeof createOwnedUser>> | undefined;

test.beforeEach(async ({ browser, baseURL }, testInfo) => {
  if (!baseURL) throw new Error('reader Literata test requires a base URL');
  const { username, email } = createOwnedUserIdentity(testInfo.project.name, testInfo.workerIndex);
  const password = `Aa7!zY9@${username.slice(-20)}`;
  const configuredRunId = testInfo.config.metadata.cwngE2ERunId;
  if (typeof configuredRunId !== 'string' || !configuredRunId) {
    throw new Error('reader Literata test requires an E2E ownership run id');
  }
  adminApi = await DirectAdminApi.open(baseURL, adminCredentialsFromEnvironment());
  ownedUser = await createOwnedUser(adminApi, baseURL, {
    name: username,
    email,
    password,
    roles: {
      admin: false,
      viewer: true,
      download: true,
      upload: false,
      edit: false,
      edit_shelfs: false,
      delete_books: false,
    },
  }, {
    runId: configuredRunId,
    workerId: `${testInfo.project.name}:${testInfo.workerIndex}:${testInfo.parallelIndex}:literata`,
  });

  // The classic reader flushes a keepalive bookmark on pagehide. Chromium
  // sends that teardown request with its native UA even when the page has a
  // Playwright device override; Flask-Login binds sessions to User-Agent.
  // Match the login and reader to Chromium's real transport UA while retaining
  // the project's viewport and touchscreen settings.
  const browserSession = await browser.newBrowserCDPSession();
  nativeUserAgent = (await browserSession.send('Browser.getVersion')).userAgent;
  await browserSession.detach();
  const projectUse = testInfo.project.use;
  readerContext = await browser.newContext({
    baseURL,
    viewport: projectUse.viewport,
    isMobile: projectUse.isMobile,
    hasTouch: projectUse.hasTouch,
    deviceScaleFactor: projectUse.deviceScaleFactor,
    userAgent: nativeUserAgent,
    locale: projectUse.locale,
    colorScheme: projectUse.colorScheme,
    storageState: { cookies: [], origins: [] },
  });
  await readerContext.addInitScript(() => {
    const scopedWindow = window as typeof window & { __literataTouchStarts?: number };
    scopedWindow.__literataTouchStarts = 0;
    document.addEventListener('touchstart', () => {
      scopedWindow.__literataTouchStarts = (scopedWindow.__literataTouchStarts ?? 0) + 1;
    }, true);
  });
  activePage = await readerContext.newPage();
  const csrfResponse = await readerContext.request.get('/api/v1/auth/csrf');
  expect(csrfResponse.ok()).toBeTruthy();
  const loginResponse = await readerContext.request.post('/api/v1/auth/login', {
    headers: { 'X-CSRFToken': ((await csrfResponse.json()) as { csrf_token: string }).csrf_token },
    data: { username, password, remember: false },
  });
  expect(loginResponse.ok(), await loginResponse.text()).toBeTruthy();
  await activePage.goto('/app');
  await expect(activePage.getByRole('button', { name: `Account: ${username}` })).toBeVisible();

  const response = await activePage.request.get('/api/v1/reader/settings');
  savedFont = ((await response.json()) as { reader: { font?: unknown } }).reader.font;
});

test.afterEach(async () => {
  try {
    if (savedFont !== undefined && activePage) {
      await activePage.request.post('/api/v1/reader/settings', {
        headers: { 'X-CSRFToken': await csrfToken(activePage) },
        data: { font: savedFont },
      });
    }
  } finally {
    await readerContext?.close().catch(() => undefined);
    readerContext = undefined;
    activePage = undefined;
    savedFont = undefined;
    nativeUserAgent = '';
    if (adminApi && ownedUser) {
      await cleanupOwnedUser(adminApi, ownedUser.ownership, ownedUser.user.id);
    }
    await adminApi?.dispose().catch(() => undefined);
    adminApi = undefined;
    ownedUser = undefined;
  }
});

function pageForTest(): Page {
  if (!activePage) throw new Error('device-matched EPUB test page was not initialized');
  return activePage;
}

async function verifyViewport(page: Page, testInfo: TestInfo): Promise<boolean> {
  const expectedSize = testInfo.project.name === 'mobile'
    ? { width: 375, height: 667 }
    : { width: 1280, height: 800 };
  const device = await page.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
    hasTouch: navigator.maxTouchPoints > 0 && 'ontouchstart' in window,
    maxTouchPoints: navigator.maxTouchPoints,
    userAgent: navigator.userAgent,
  }));
  expect(device.userAgent).toBe(nativeUserAgent);
  expect(device).toMatchObject(expectedSize);
  if (testInfo.project.name === 'mobile') {
    expect(device.hasTouch).toBe(true);
    expect(device.maxTouchPoints).toBeGreaterThan(0);
  }
  const contextEvidence = JSON.stringify({ project: testInfo.project.name, ...device }, null, 2);
  await writeFile(testInfo.outputPath('literata-browser-context.json'), contextEvidence);
  await test.info().attach('literata-browser-context.json', {
    body: contextEvidence,
    contentType: 'application/json',
  });
  return testInfo.project.name === 'mobile';
}

async function openAppearance(page: Page, useTouch: boolean): Promise<void> {
  const button = page.getByRole('button', { name: 'Reading appearance' });
  if (useTouch) {
    const box = await button.boundingBox();
    expect(box).toBeTruthy();
    await page.touchscreen.tap(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await expect.poll(() => page.evaluate(() =>
      (window as typeof window & { __literataTouchStarts?: number }).__literataTouchStarts ?? 0,
    )).toBeGreaterThan(0);
  } else {
    await button.click();
  }
}

async function captureReader(page: Page, testInfo: TestInfo, reader: 'spa' | 'classic'): Promise<void> {
  const viewport = testInfo.project.name === 'mobile' ? 'phone375' : 'desktop1280';
  await page.screenshot({
    path: testInfo.outputPath(`literata-${reader}-${viewport}.jpg`),
    type: 'jpeg', quality: 85, fullPage: false,
  });
}

test('SPA applies Literata in the EPUB document and serves the bundled font', async ({}, testInfo) => {
  const page = pageForTest();
  const useTouch = await verifyViewport(page, testInfo);
  const id = await firstEpubId(page);
  await openFixture(page, id, false);

  const fontResponse = page.waitForResponse((response) =>
    response.url().includes(REGULAR_FONT_PATH) && response.status() === 200,
  );
  await openAppearance(page, useTouch);
  await page.getByLabel('Font family').selectOption('Literata');
  await assertFontRequest(fontResponse);

  await expect.poll(() => savedReaderFont(page)).toBe('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);

  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Reading appearance' })).toBeHidden();
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  await page.getByRole('button', { name: 'Target Chapter', exact: true }).click();
  await expect.poll(async () => page.locator('iframe').first().evaluate((element) =>
    (element as HTMLIFrameElement).contentDocument?.body?.innerText ?? '',
  ), { timeout: 20_000 }).toContain('NOTEREF-SECTION-2');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);

  await page.reload();
  await expect(page.locator('iframe').first()).toBeAttached({ timeout: 30_000 });
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(() => savedReaderFont(page)).toBe('Literata');
  await captureReader(page, testInfo, 'spa');
});

test('classic EPUB reader applies and persists Literata using the served font', async ({}, testInfo) => {
  const page = pageForTest();
  const useTouch = await verifyViewport(page, testInfo);
  const id = await firstEpubId(page);
  await openFixture(page, id, true);

  const fontResponse = page.waitForResponse((response) =>
    response.url().includes(REGULAR_FONT_PATH) && response.status() === 200,
  );
  if (useTouch) {
    const button = page.locator('#setting');
    const box = await button.boundingBox();
    expect(box).toBeTruthy();
    await page.touchscreen.tap(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await expect.poll(() => page.evaluate(() =>
      (window as typeof window & { __literataTouchStarts?: number }).__literataTouchStarts ?? 0,
    )).toBeGreaterThan(0);
  } else {
    await page.locator('#setting').click();
  }
  await page.locator('#settings-modal').getByRole('button', { name: 'Literata', exact: true }).click();
  await assertFontRequest(fontResponse);

  await expect.poll(() => savedReaderFont(page)).toBe('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);

  await page.locator('#settings-modal .closer').click();
  await page.locator('#slider').click();
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  const targetChapter = page.locator('#tocView a.toc_link').filter({ hasText: 'Target Chapter' });
  await expect(targetChapter).toBeVisible();
  await targetChapter.click();
  await expect.poll(async () => page.locator('iframe').first().evaluate((element) =>
    (element as HTMLIFrameElement).contentDocument?.body?.innerText ?? '',
  ), { timeout: 20_000 }).toContain('NOTEREF-SECTION-2');
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);

  await page.reload();
  await expect(page.locator('iframe').first()).toBeAttached({ timeout: 30_000 });
  await expect.poll(async () => (await renderedLiterata(page)).loaded).toBe(true);
  await expect.poll(async () => (await renderedLiterata(page)).family).toContain('Literata');
  await expect.poll(() => savedReaderFont(page)).toBe('Literata');
  await captureReader(page, testInfo, 'classic');
});
