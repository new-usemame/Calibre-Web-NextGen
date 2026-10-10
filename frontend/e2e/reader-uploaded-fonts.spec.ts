import AxeBuilder from '@axe-core/playwright';
import { expect, test } from './fixtures';
import type { BrowserContext, Page, TestInfo } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cleanupOwnedUser, createOwnedUser, createOwnedUserIdentity } from './user-reaper';
import { adminCredentialsFromEnvironment, DirectAdminApi } from './direct-admin-api';

const EPUB_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/test_noteref_links.epub',
);
const FONT_FIXTURE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/cwng-owned-test-font.ttf');
let uploadedFont: { id: string; family: string; label: string; url: string } | undefined;
let managerContext: BrowserContext | undefined;
let managerPage: Page | undefined;
let uploadedBytes: Buffer;
let captureRoot = '';

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

async function renderedFont(page: Page): Promise<{ family: string; loaded: boolean }> {
  return page.locator('iframe').first().evaluate((element, family) => {
    const doc = (element as HTMLIFrameElement).contentDocument!;
    return { family: doc.defaultView!.getComputedStyle(doc.querySelector('p')!).fontFamily,
      loaded: Array.from(doc.fonts).some(face => face.family.replace(/["']/g, '') === family && face.status === 'loaded') };
  }, uploadedFont!.family);
}

let savedFont: unknown;
let readerContext: BrowserContext | undefined;
let activePage: Page | undefined;
let nativeUserAgent = '';
let adminApi: DirectAdminApi | undefined;
let ownedUser: Awaited<ReturnType<typeof createOwnedUser>> | undefined;

test.beforeEach(async ({ browser, baseURL }, testInfo) => {
  if (!baseURL) throw new Error('uploaded-font reader test requires a base URL');
  const { username, email } = createOwnedUserIdentity(testInfo.project.name, testInfo.workerIndex);
  const password = `Aa7!zY9@${username.slice(-20)}`;
  const configuredRunId = testInfo.config.metadata.cwngE2ERunId;
  if (typeof configuredRunId !== 'string' || !configuredRunId) {
    throw new Error('uploaded-font reader test requires an E2E ownership run id');
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
  managerContext = await browser.newContext({ baseURL, userAgent: nativeUserAgent,
    viewport: projectUse.viewport, isMobile: projectUse.isMobile, hasTouch: projectUse.hasTouch });
  managerPage = await managerContext.newPage();
  const adminCsrf = await csrfToken(managerPage);
  const credentials = adminCredentialsFromEnvironment();
  const login = await managerContext.request.post('/api/v1/auth/login', {
    headers: { 'X-CSRFToken': adminCsrf }, data: { ...credentials, remember: false },
  });
  expect(login.ok(), await login.text()).toBeTruthy();
  uploadedBytes = Buffer.concat([await readFile(FONT_FIXTURE), Buffer.from(username)]);
  captureRoot = testInfo.outputPath('captures');
  await mkdir(captureRoot, { recursive: true });
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
    if (uploadedFont && managerPage) {
      await managerPage.request.delete(`/api/v1/admin/reader/fonts/${uploadedFont.id.slice(7)}`, {
        headers: { 'X-CSRFToken': await csrfToken(managerPage) },
      });
    }
    uploadedFont = undefined;
    await managerContext?.close().catch(() => undefined);
    managerContext = undefined; managerPage = undefined;
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
    path: path.join(captureRoot, `uploaded-${reader}-${viewport}.jpg`),
    type: 'jpeg', quality: 85, fullPage: false,
  });
}


async function uploadThroughUi(testInfo: TestInfo) {
  const manager = managerPage!;
  await manager.goto('/app/admin#reader-fonts');
  const section = manager.locator('#reader-fonts');
  await expect(section.getByRole('heading', { name: 'Reader fonts', exact: true })).toBeVisible();
  await expect(section.getByLabel(/^Font file/)).toBeVisible();
  await manager.evaluate(theme => document.documentElement.dataset.theme = theme, testInfo.project.name === 'mobile' ? 'dark' : 'light');
  await expect(manager.locator('html')).toHaveAttribute('data-theme', testInfo.project.name === 'mobile' ? 'dark' : 'light');
  // The signature alone is insufficient; the real packaged parser rejects this malformed TTF.
  await section.getByLabel(/^Font file/).setInputFiles({ name: 'broken.ttf', mimeType: 'font/ttf', buffer: Buffer.from([0,1,0,0,4,5,6,7]) });
  await section.getByRole('button', { name: 'Upload font', exact: true }).click();
  await expect(section.locator('#reader-font-message')).toContainText(/not a usable font|could not be read/);
  await expect(section.getByLabel(/^Font file/)).toHaveAttribute('aria-invalid', 'true');
  const label = `Owned font ${ownedUser!.user.name}`;
  await section.getByLabel(/^Font file/).setInputFiles({ name: 'owned.ttf', mimeType: 'font/ttf', buffer: uploadedBytes });
  await section.getByLabel('Display name (optional)').fill(label);
  const uploadResponse = manager.waitForResponse(r => r.url().endsWith('/api/v1/admin/reader/fonts') && r.request().method() === 'POST');
  await section.getByRole('button', { name: 'Upload font', exact: true }).click();
  const response = await uploadResponse;
  expect(response.status(), await response.text()).toBe(201);
  uploadedFont = (await response.json()).item;
  expect(uploadedFont!.label).toBe(label);
  await expect(section.getByText('Font is available in both EPUB readers.')).toBeVisible();
  await expect(section.getByRole('button', { name: `Remove ${label}`, exact: true })).toBeVisible();
  await section.scrollIntoViewIfNeeded();
  expect((await new AxeBuilder({ page: manager }).include('#reader-fonts').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()).violations).toEqual([]);
  expect(await manager.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await manager.screenshot({ path: path.join(captureRoot, `uploaded-admin-${testInfo.project.name}.jpg`), type: 'jpeg', quality: 80 });
  const duplicate = await manager.request.post('/api/v1/admin/reader/fonts', {
    headers: { 'X-CSRFToken': await csrfToken(manager) },
    multipart: { file: { name: 'different.ttf', mimeType: 'font/ttf', buffer: uploadedBytes }, name: 'Different label' },
  });
  expect(duplicate.status()).toBe(200);
  expect((await duplicate.json()).item.id).toBe(uploadedFont!.id);
  const forbidden = await pageForTest().request.delete(`/api/v1/admin/reader/fonts/${uploadedFont!.id.slice(7)}`, {
    headers: { 'X-CSRFToken': await csrfToken(pageForTest()) },
  });
  expect(forbidden.status()).toBe(403);
}

for (const classic of [false, true]) {
  test(`${classic ? 'Classic' : 'New UI'} uses uploaded font across chapters/reload and safely falls back after removal`, async ({}, testInfo) => {
    test.setTimeout(120_000);
    await uploadThroughUi(testInfo);
    const page = pageForTest();
    const useTouch = await verifyViewport(page, testInfo);
    const id = await firstEpubId(page);
    await openFixture(page, id, classic);
    const fontResponse = page.waitForResponse(r => r.url().endsWith(uploadedFont!.url) && r.status() === 200);
    if (classic) {
      if (useTouch) await page.locator('#setting').tap(); else await page.locator('#setting').click();
      await page.locator(`[data-font="${uploadedFont!.id}"]`).click();
    } else {
      await openAppearance(page, useTouch);
      await page.getByLabel('Font family').selectOption(uploadedFont!.id);
    }
    const response = await fontResponse;
    expect(response.headers()['content-type']).toContain('font/ttf');
    expect(response.headers()['cache-control']).toContain('private');
    expect(createHash('sha256').update(await response.body()).digest('hex')).toBe(createHash('sha256').update(uploadedBytes).digest('hex'));
    await expect.poll(() => savedReaderFont(page)).toBe(uploadedFont!.id);
    await expect.poll(async () => (await renderedFont(page)).family).toContain(uploadedFont!.family);
    await expect.poll(async () => (await renderedFont(page)).loaded).toBe(true);
    if (classic) {
      await page.locator('#settings-modal .closer').click();
      await page.locator('#slider').click();
      await page.locator('#tocView a.toc_link').filter({ hasText: 'Target Chapter' }).click();
    } else {
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Table of contents', exact: true }).click();
      await page.getByRole('button', { name: 'Target Chapter', exact: true }).click();
    }
    await expect.poll(async () => page.locator('iframe').first().evaluate(element => (element as HTMLIFrameElement).contentDocument?.body?.innerText ?? ''), { timeout: 20_000 }).toContain('NOTEREF-SECTION-2');
    await expect.poll(async () => (await renderedFont(page)).loaded).toBe(true);
    await page.reload();
    await expect(page.locator('iframe').first()).toBeAttached({ timeout: 30_000 });
    await expect.poll(async () => (await renderedFont(page)).loaded).toBe(true);
    await captureReader(page, testInfo, classic ? 'classic' : 'spa');
    const manager = managerPage!;
    await manager.getByRole('button', { name: `Remove ${uploadedFont!.label}`, exact: true }).click();
    const dialog = manager.getByRole('dialog', { name: `Remove ${uploadedFont!.label}?`, exact: true });
    await expect(dialog).toBeVisible();
    const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
    const remove = dialog.getByRole('button', { name: 'Remove font', exact: true });
    await expect(cancel).toBeFocused();
    await manager.keyboard.press('Shift+Tab'); await expect(remove).toBeFocused();
    await manager.keyboard.press('Tab'); await expect(cancel).toBeFocused();
    expect((await new AxeBuilder({ page: manager }).include('[role="dialog"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()).violations).toEqual([]);
    await manager.screenshot({ path: path.join(captureRoot, `uploaded-remove-${testInfo.project.name}.jpg`), type: 'jpeg', quality: 80 });
    await manager.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(manager.getByRole('button', { name: `Remove ${uploadedFont!.label}`, exact: true })).toBeFocused();
    await manager.getByRole('button', { name: `Remove ${uploadedFont!.label}`, exact: true }).click();
    await dialog.getByRole('button', { name: 'Remove font', exact: true }).click();
    const removalMessage = manager.locator('#reader-font-message');
    await expect(removalMessage).toHaveText('Font removed.');
    await expect(removalMessage).toBeVisible();
    await expect(manager.locator('#reader-fonts-heading')).toBeFocused();
    await page.reload();
    await expect(page.locator('iframe').first()).toBeAttached({ timeout: 30_000 });
    await expect.poll(() => savedReaderFont(page)).toBe('default');
    await expect.poll(async () => (await renderedFont(page)).family).not.toContain(uploadedFont!.family);
    const removed = await page.request.get(uploadedFont!.url);
    expect(removed.status()).toBe(404);
  });
}

for (const builtin of ['Arial', 'Literata']) {
 test(`New UI preserves ${builtin} when the optional catalog is unavailable`, async ({}, testInfo) => {
  const page = pageForTest();
  await verifyViewport(page, testInfo);
  const save = await page.request.post('/api/v1/reader/settings', {
    headers: { 'X-CSRFToken': await csrfToken(page) }, data: { font: builtin },
  });
  expect(save.ok()).toBeTruthy();
  await page.route('**/api/v1/reader/fonts', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ error: { message: 'catalog unavailable' } }),
  }));
  await openFixture(page, await firstEpubId(page), false);
  await expect.poll(() => page.locator('iframe').first().evaluate(element => {
    const doc = (element as HTMLIFrameElement).contentDocument!;
    return doc.defaultView!.getComputedStyle(doc.querySelector('p')!).fontFamily;
  })).toContain(builtin);
  if (builtin === 'Literata') {
    await expect.poll(() => page.locator('iframe').first().evaluate(element => {
      const doc = (element as HTMLIFrameElement).contentDocument!;
      return Array.from(doc.fonts).some(face => face.family.replace(/["']/g, '') === 'Literata'
        && face.weight === 'normal' && face.style === 'normal' && face.status === 'loaded');
    })).toBe(true);
  }
  await openAppearance(page, testInfo.project.name === 'mobile');
  await expect(page.getByLabel('Font family')).toHaveValue(builtin);
  await expect(page.getByLabel('Font family').locator('option')).toHaveCount(6);
  await expect(page.getByLabel('Font family').locator('option[value="Literata"]')).toHaveText('Literata');
  await expect(page.getByText('Could not load reader fonts.', { exact: true })).toBeVisible();
  expect(await savedReaderFont(page)).toBe(builtin);
});

}
