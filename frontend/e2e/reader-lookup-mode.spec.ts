import { test, expect } from './fixtures';
import { devices, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import JSZip from 'jszip';

let touchDeviceContext: BrowserContext | undefined;
let cleanupAdminPage: Page | undefined;
let uploadedBookId: number | undefined;
let uploadedFilename: string | undefined;
let uploadedTitle: string | undefined;
const READER_LOOKUP_EPUB = path.resolve(
  process.cwd(),
  '../tests/fixtures/sample_books/reader_lookup_2400.epub',
);

test.afterEach(async () => {
  if (cleanupAdminPage && uploadedFilename) {
    // Import runs asynchronously. If the test failed while waiting for the
    // ingest task, make one bounded cleanup attempt by its unique source name.
    if (uploadedBookId === undefined) {
      uploadedBookId = await findReaderFixture(cleanupAdminPage, uploadedFilename, uploadedTitle!, 15_000)
        .catch(() => undefined);
    }
    if (uploadedBookId !== undefined) {
      const csrf = await csrfToken(cleanupAdminPage);
      const deleted = await cleanupAdminPage.request.post(
        `/api/v1/books/${uploadedBookId}/delete`,
        { headers: { 'X-CSRFToken': csrf } },
      );
      expect(deleted.status(), 'delete the temporary reader lookup book').toBe(204);
    }
  }
  await touchDeviceContext?.close();
  touchDeviceContext = undefined;
  cleanupAdminPage = undefined;
  uploadedBookId = undefined;
  uploadedFilename = undefined;
  uploadedTitle = undefined;
});

async function capture(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}-${info.project.name}.jpg`);
  await page.screenshot({ path, type: 'jpeg', quality: 75, animations: 'disabled' });
  await info.attach(name, { path, contentType: 'image/jpeg' });
}
async function detail(page: Page, id: number) {
  const response = await page.request.get(`/api/v1/books/${id}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function bookmark(page: Page, id: number) {
  return (await (await page.request.get(`/api/v1/books/${id}/bookmark?format=epub`)).json()).bookmark;
}
async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return (await response.json()).csrf_token;
}

async function findReaderFixture(page: Page, filename: string, title: string, timeout: number): Promise<number> {
  let found: number | undefined;
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/books?per_page=100&sort=new');
    if (!response.ok()) return false;
    const items = (await response.json()).items ?? [];
    for (const item of items) {
      if (item.title !== title) continue;
      const book = await detail(page, item.id);
      if (book.original_filename === filename) {
        found = item.id;
        return true;
      }
    }
    return false;
  }, { timeout, intervals: [500, 1000, 2000] }).toBe(true);
  if (found === undefined) throw new Error(`Uploaded EPUB ${filename} was not indexed.`);
  return found;
}

async function uploadReaderFixture(adminPage: Page, testInfo: TestInfo): Promise<number> {
  const me = await adminPage.request.get('/api/v1/auth/me');
  expect(me.ok()).toBeTruthy();
  expect((await me.json()).role.admin, 'the E2E upload/cleanup session is an administrator').toBe(true);

  uploadedFilename = `reader-lookup-2400-${testInfo.project.name}-${testInfo.parallelIndex}-${randomUUID()}.epub`;
  const fixtureId = randomUUID();
  uploadedTitle = `CWNG 2400 Reader Lookup Fixture ${fixtureId.slice(0, 12)}`;
  cleanupAdminPage = adminPage;
  const zip = await JSZip.loadAsync(await readFile(READER_LOOKUP_EPUB));
  const packageFile = zip.file('EPUB/package.opf');
  if (!packageFile) throw new Error('The reader lookup EPUB fixture has no package document.');
  const packageDocument = await packageFile.async('string');
  const packageId = `urn:uuid:${fixtureId}`;
  if (!packageDocument.includes('urn:uuid:cwng-2400-reader-lookup-fixture')) {
    throw new Error('The reader lookup EPUB fixture has no expected identifier.');
  }
  if (!packageDocument.includes('<dc:title>CWNG 2400 Reader Lookup Fixture</dc:title>')) {
    throw new Error('The reader lookup EPUB fixture has no expected title.');
  }
  zip.file(
    'EPUB/package.opf',
    packageDocument
      .replace('urn:uuid:cwng-2400-reader-lookup-fixture', packageId)
      .replace('<dc:title>CWNG 2400 Reader Lookup Fixture</dc:title>', `<dc:title>${uploadedTitle}</dc:title>`),
  );
  // Calibre deduplicates identical uploads. A unique package identifier keeps
  // this test's real EPUB import owned by this run even when desktop/mobile or
  // CI retries upload the same deterministic chapters in parallel.
  const file = await zip.generateAsync({ type: 'nodebuffer', compression: 'STORE' });
  const response = await adminPage.request.post('/api/v1/upload', {
    headers: { 'X-CSRFToken': await csrfToken(adminPage) },
    multipart: {
      file: {
        name: uploadedFilename,
        mimeType: 'application/epub+zip',
        buffer: file,
      },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  expect((await response.json()).queued).toContain(uploadedFilename);
  uploadedBookId = await findReaderFixture(adminPage, uploadedFilename, uploadedTitle, 60_000);
  return uploadedBookId;
}

async function readableEpub(page: Page, adminPage: Page, testInfo: TestInfo) {
  const id = await uploadReaderFixture(adminPage, testInfo);
  const book = await detail(page, id);
  const format = book.formats.find((f: { format: string }) => f.format.toLowerCase() === 'epub');
  if (!format || !(await page.request.get(format.content_url || `/show/${id}/epub`)).ok()) {
    throw new Error('The owned reader lookup EPUB was not available after ingest.');
  }
  return id;
}
async function readerReady(page: Page) {
  await expect(page.locator('iframe').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Next page', exact: true })).toBeVisible();
}

async function classicCfi(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    const reader = (window as any).reader;
    return reader?.rendition?.currentLocation?.()?.start?.cfi ?? null;
  });
}

async function classicRenderedText(page: Page): Promise<string> {
  const texts: string[] = [];
  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    try {
      texts.push(await frame.locator('body').innerText({ timeout: 1000 }));
    } catch { /* an EPUB section can be replaced while we inspect it */ }
  }
  return texts.join('\n').replace(/\s+/g, ' ').trim();
}

async function classicProgress(page: Page): Promise<number> {
  const text = await page.locator('#progress').innerText();
  return Number.parseInt(text, 10) || 0;
}

async function turnClassicPage(page: Page, before: string | null) {
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 600) {
    // The reader iframe covers the classic arrow controls on a phone. Use the
    // real touch affordance on the visible EPUB viewport. EPUB.js lays out a
    // multi-page iframe wider than the viewport, so its full bounding box can
    // extend far off-screen; use the clipped viewport halves for x coordinates
    // and the visible iframe/viewport intersection for y.
    const frame = page.locator('#viewer iframe').first();
    const box = await frame.boundingBox();
    if (!box) throw new Error('Classic EPUB frame has no visible bounds.');
    const minY = Math.max(0, box.y);
    const maxY = Math.min(viewport.height, box.y + box.height);
    if (maxY <= minY) throw new Error('Classic EPUB frame does not intersect the mobile viewport.');
    const touchY = minY + (maxY - minY) * 0.5;
    const countTrustedTouchEnds = async () => {
      const perFrame = await Promise.all(page.frames().map(async (frame) => {
        try {
          return await frame.evaluate(() => {
            const scope = window as any;
            if (!scope.__e2eTouchCounterInstalled) {
              scope.__e2eTouchCounterInstalled = true;
              scope.__e2eTrustedTouchEndCount = 0;
              window.addEventListener('touchend', (event) => {
                if (event.isTrusted) scope.__e2eTrustedTouchEndCount += 1;
              }, true);
            }
            return scope.__e2eTrustedTouchEndCount as number;
          });
        } catch { return 0; }
      }));
      return perFrame.reduce((total, count) => total + count, 0);
    };
    const touchCountBefore = await countTrustedTouchEnds();
    const rtl = await page.evaluate(() =>
      (window as any).reader?.book?.package?.metadata?.direction === 'rtl');
    for (const fraction of [rtl ? 0.1 : 0.9, rtl ? 0.9 : 0.1]) {
      await page.touchscreen.tap(viewport.width * fraction, touchY);
      try {
        await expect.poll(() => classicCfi(page), { timeout: 2_500 }).not.toBe(before);
      } catch { /* this side can be the previous-page half for this EPUB */ }
      if (await classicCfi(page) !== before) {
        const touchCountAfter = await countTrustedTouchEnds();
        expect(touchCountAfter, 'page turn must be caused by a touch event in the EPUB rendition').toBeGreaterThan(touchCountBefore);
        return 'touch';
      }
    }
    throw new Error(`Mobile touch did not turn the classic EPUB page; CFI stayed ${before}.`);
  } else {
    await page.locator('#next').click();
    await expect.poll(() => classicCfi(page), { timeout: 5_000 }).not.toBe(before);
    return 'button';
  }
}

async function classicLocalPosition(page: Page, key?: string) {
  return page.evaluate(savedKey => {
    const bookUrl = savedKey || (window as any).calibre?.bookUrl;
    if (!bookUrl) return null;
    return {
      progress: localStorage.getItem(`calibre.reader.progress.${bookUrl}`),
      cfi: localStorage.getItem(`calibre.reader.cfi.${bookUrl}`),
    };
  }, key);
}

async function moveClassicReader(page: Page, until: (progress: number, cfi: string | null) => boolean) {
  const next = page.locator('#next');
  await expect(next).toBeVisible();
  const methods: string[] = [];
  for (let turn = 0; turn < 16; turn++) {
    const before = await classicCfi(page);
    methods.push(await turnClassicPage(page, before));
    if (until(await classicProgress(page), await classicCfi(page))) return methods;
  }
  throw new Error(`Classic reader did not reach the requested state after 16 page turns (progress ${await classicProgress(page)}%).`);
}

test('lookup survives navigation without replacing the saved place or Reading marker', async ({ page: adminPage, secondaryUser, browser, baseURL }, testInfo) => {
  test.setTimeout(180_000);
  if (!baseURL) throw new Error('Reader lookup proof requires the fixture base URL.');
  // Chromium sends pagehide keepalive fetches with its native User-Agent even
  // when its page has a Playwright device override. Flask-Login binds a strong
  // session to that value, so use the native UA for both login and the real
  // reader while retaining the mobile viewport/touch device options below.
  const browserSession = await browser.newBrowserCDPSession();
  const nativeUserAgent = (await browserSession.send('Browser.getVersion')).userAgent;
  await browserSession.detach();
  touchDeviceContext = await browser.newContext({
    ...(testInfo.project.name === 'mobile' ? devices['iPhone 13'] : {}),
    baseURL,
    userAgent: nativeUserAgent,
    viewport: testInfo.project.name === 'mobile'
      ? { width: 375, height: 667 }
      : { width: 1280, height: 800 },
    storageState: { cookies: [], origins: [] },
  });
  const page = await touchDeviceContext.newPage();
  await page.goto('/app/login');
  await page.locator('input[autocomplete="username"]').fill(secondaryUser.username);
  await page.locator('input[autocomplete="current-password"]').fill(secondaryUser.password);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/app(\/|$|\?)/, { timeout: 20_000 });
  await expect(page.getByRole('button', { name: `Account: ${secondaryUser.username}` })).toBeVisible();
  await page.setViewportSize(testInfo.project.name === 'mobile' ? { width: 375, height: 667 } : { width: 1280, height: 800 });
  const id = await readableEpub(page, adminPage, testInfo);
  // Establish a real saved position first, scoped to this test's owned account.
  await page.goto(`/app/read/${id}`);
  await readerReady(page);
  for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
  await expect.poll(async () => {
    if ((await detail(page, id)).in_progress) return true;
    // EPUB locations generate asynchronously; an early saved CFI can lack a
    // percentage. Turn real pages until the server records a Reading marker.
    await page.keyboard.press('ArrowRight');
    return false;
  }, { timeout: 20_000, intervals: [1500] }).toBe(true);
  await expect.poll(() => bookmark(page, id), { timeout: 20_000 }).toBeTruthy();
  await page.goto(`/app/book/${id}`);
  await expect(page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  const saved = await bookmark(page, id);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: 'Remove from Currently Reading', exact: true })).toBeVisible();
  await capture(page, testInfo, 'new-ui-reading-actions');
  await page.getByRole('menuitem', { name: 'Remove from Currently Reading', exact: true }).click();
  await expect.poll(async () => (await detail(page, id)).in_progress).toBe(false);
  expect(await bookmark(page, id)).toBe(saved);
  const state = await detail(page, id);
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/bookmark(?:\/|\?|$)|\/(?:read|stop-reading)(?:\?|$)|\/ajax\/stopreading\//.test(request.url())) writes.push(request.url());
  });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Open without saving progress', exact: true }).click();
  await expect(page).toHaveURL(/lookup=1/);
  await readerReady(page);
  await expect(page.getByText('Progress is not being saved.', { exact: true })).toBeVisible();
  for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight');
  for (const theme of ['Light', 'Dark']) {
    await page.getByRole('button', { name: 'Reading appearance', exact: true }).click();
    await page.getByRole('button', { name: theme, exact: true }).click();
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(page.getByText('Progress is not being saved.', { exact: true })).toBeVisible();
    await capture(page, testInfo, `new-ui-lookup-${theme.toLowerCase()}`);
  }
  // A TOC jump exits the transient highlight preview; lookup must survive it.
  await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
  const contents = page.getByRole('navigation', { name: 'Table of contents', exact: true });
  const chapters = contents.getByRole('button');
  if (await chapters.count() > 2) await chapters.last().click();
  else await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
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
  await expect.poll(async () => {
    if ((await detail(page, id)).in_progress) return true;
    await page.keyboard.press('ArrowRight');
    return false;
  }, { timeout: 20_000, intervals: [1500] }).toBe(true);

  // Start the classic-reader half at the beginning. The SPA has already
  // proved ordinary persistence above; keeping its CFI here would make this
  // test depend on two different epub.js builds interpreting the same CFI
  // identically before it reaches the classic lookup behavior under test.
  const resetBookmark = await page.request.post(`/api/v1/books/${id}/bookmark`, {
    headers: { 'X-CSRFToken': await csrfToken(page), 'Content-Type': 'application/json' },
    data: { format: 'epub', bookmark: '' },
  });
  expect(resetBookmark.status()).toBe(204);

  await page.goto(`/app/book/${id}`);
  await page.context().addCookies([{ name: 'cwng_prefer_spa', value: '0', url: new URL(page.url()).origin }]);
  await page.goto(`/book/${id}`);
  const normalClassicRead = page.getByRole('link', { name: 'Read now', exact: true });
  await expect(normalClassicRead).toBeVisible();
  const normalClassicUrl = await normalClassicRead.getAttribute('href');
  expect(normalClassicUrl).toBeTruthy();
  await page.goto(normalClassicUrl!);
  await expect(page.locator('#reader-lookup-banner')).toHaveCount(0);
  await expect(page.locator('#viewer iframe').first()).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => classicRenderedText(page).then((text) => text.length), {
    timeout: 30_000,
    message: 'Normal classic EPUB should render book text before progress is measured',
  }).toBeGreaterThan(120);
  const normalClassicStartCfi = await classicCfi(page);
  const normalClassicBookmark = await bookmark(page, id);
  const normalClassicNavigation = await moveClassicReader(
    page,
    (_progress, cfi) => !!cfi && cfi !== normalClassicStartCfi,
  );
  if ((page.viewportSize()?.width ?? Infinity) <= 600) {
    expect(normalClassicNavigation.some((method) => method.startsWith('touch'))).toBe(true);
  }
  await expect.poll(() => bookmark(page, id), { timeout: 20_000 }).not.toBe(normalClassicBookmark);
  await capture(page, testInfo, 'classic-normal-reader');
  const classicBookUrl = await page.evaluate(() => (window as any).calibre.bookUrl as string);

  await page.goto(`/book/${id}`);
  // Take the baseline after leaving the ordinary reader: its pagehide save
  // belongs to normal reading, not to the lookup session being measured.
  const authenticatedAfterClassicPagehide = await page.request.get('/api/v1/auth/me');
  expect(authenticatedAfterClassicPagehide.ok(), 'classic page navigation must retain the reader session').toBeTruthy();
  expect((await authenticatedAfterClassicPagehide.json()).name).toBe(secondaryUser.username);
  const classicLookupLocalBefore = await classicLocalPosition(page, classicBookUrl);
  await expect(page.locator('#currently-reading-badge')).toBeVisible();
  const classicSaved = await bookmark(page, id);
  await capture(page, testInfo, 'classic-reading-actions');
  await page.getByRole('button', { name: 'Remove from Currently Reading', exact: true }).click();
  await expect(page.locator('#currently-reading-badge')).toHaveCount(0);
  expect(await bookmark(page, id)).toBe(classicSaved);
  const classicState = await detail(page, id);
  const lookupLink = page.getByRole('link', { name: 'Open without saving progress', exact: true });
  await expect(lookupLink).toBeVisible();
  const lookupUrl = await lookupLink.getAttribute('href');
  expect(lookupUrl).toContain('lookup=1');
  writes.length = 0;
  await page.goto(lookupUrl!);
  await expect(page.locator('.reader-lookup-banner')).toHaveText('Progress is not being saved.');
  await expect(page.locator('#viewer iframe').first()).toBeVisible({ timeout: 30_000 });
  // An iframe element alone only proves the reader shell rendered. Require
  // actual prose visible to the reader in its live document before capture.
  await expect.poll(() => classicRenderedText(page).then((text) => text.length), {
    timeout: 30_000,
    message: 'Classic EPUB lookup should render book text inside its content iframe',
  }).toBeGreaterThan(120);
  const lookupCfi = await classicCfi(page);
  expect(lookupCfi, 'classic EPUB should expose its current CFI').toBeTruthy();
  const lookupNavigation = await moveClassicReader(page, (_progress, cfi) => !!cfi && cfi !== lookupCfi);
  if ((page.viewportSize()?.width ?? Infinity) <= 600) {
    expect(lookupNavigation.some((method) => method.startsWith('touch'))).toBe(true);
  }
  await expect.poll(() => classicRenderedText(page).then((text) => text.length)).toBeGreaterThan(120);
  const classicLookupLocalAfter = await classicLocalPosition(page);
  expect(classicLookupLocalAfter).toEqual(classicLookupLocalBefore);
  await capture(page, testInfo, 'classic-lookup-reader');
  await page.waitForTimeout(1800);
  await page.goto(`/book/${id}`);
  expect(writes).toEqual([]);
  expect(await bookmark(page, id)).toBe(classicSaved);
  const classicAfter = await detail(page, id);
  expect([classicAfter.read, classicAfter.in_progress, classicAfter.kosync_progress]).toEqual(
    [classicState.read, classicState.in_progress, classicState.kosync_progress]);
});
