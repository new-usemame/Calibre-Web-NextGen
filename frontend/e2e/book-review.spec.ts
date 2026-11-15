import AxeBuilder from '@axe-core/playwright';
import { expect, test } from './fixtures';
import type { BrowserContext, Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { createOwnedUser, createOwnedUserIdentity, cleanupOwnedUser } from './user-reaper';
import { DirectAdminApi, adminCredentialsFromEnvironment } from './direct-admin-api';

// Real per-account persistence; only an explicit failed mutation is routed.
let admin: DirectAdminApi;
let owner: Awaited<ReturnType<typeof createOwnedUser>>;
let context: BrowserContext;
let active: Page;
let bookId: number;
let original: Record<string, unknown>;
let captures: string;
test.describe.configure({ mode: 'serial' });
async function token(page: Page) { return (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token as string; }
async function note(page: Page) { return (await (await page.request.get(`/api/v1/books/${bookId}/review`)).json()).review as { text: string } | null; }

test.beforeEach(async ({ browser, baseURL }, info) => {
  if (!baseURL) throw new Error('book reviews require the owned rig');
  admin = await DirectAdminApi.open(baseURL, adminCredentialsFromEnvironment());
  const { username, email } = createOwnedUserIdentity(info.project.name, info.workerIndex);
  const password = `Aa7!${username.slice(-22)}`;
  owner = await createOwnedUser(admin, baseURL, { name: username, email, password,
    roles: { admin: false, viewer: true, download: false, upload: false, edit: false, edit_shelfs: false, delete_books: false } },
    { runId: String(info.config.metadata.cwngE2ERunId), workerId: `${info.project.name}:${info.workerIndex}:book-review` });
  const use = info.project.use;
  context = await browser.newContext({ baseURL, viewport: use.viewport, hasTouch: use.hasTouch,
    isMobile: use.isMobile, userAgent: use.userAgent, deviceScaleFactor: use.deviceScaleFactor });
  active = await context.newPage();
  const response = await active.request.post('/api/v1/auth/login', { headers: { 'X-CSRFToken': await token(active) }, data: { username, password, remember: false } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const theme = await active.request.post('/api/v1/account/profile', { headers: { 'X-CSRFToken': await token(active) }, data: { theme: info.project.name === 'desktop' ? 'light' : 'dark' } });
  expect(theme.ok(), await theme.text()).toBeTruthy();
  const books = await (await active.request.get('/api/v1/books?per_page=2')).json();
  expect(books.items.length).toBeGreaterThan(0); bookId = books.items[0].id;
  original = await (await active.request.get(`/api/v1/books/${bookId}`)).json();
  captures = info.outputPath('captures'); await mkdir(captures, { recursive: true });
});
test.afterEach(async ({ baseURL }) => {
  await context?.close();
  if (owner && admin && baseURL) await cleanupOwnedUser(admin, owner.ownership, owner.user.id);
  await admin?.dispose();
});

for (const classic of [false, true]) {
  test(`${classic ? 'Classic' : 'New UI'} private review survives retry, reload and removal without metadata rights`, async ({}, info) => {
    const p = active;
    await p.goto(classic ? `/book/${bookId}` : `/app/book/${bookId}`);
    const region = p.locator(classic ? '#private-book-review' : '[data-testid="book-review"]');
    const edit = region.getByRole('button', { name: 'Add a review', exact: true });
    await expect(edit).toBeVisible();
    if (classic) {
      await expect(region.getByRole('button', { name: 'Retry', exact: true })).not.toBeVisible();
      await expect(region.locator('#private-review-confirm')).not.toBeVisible();
      // Classic also serves this page as an XHR fragment after DOM readiness.
      const fragment = await p.request.get(`/book/${bookId}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      expect(fragment.ok(), `XHR fragment HTTP ${fragment.status()}: ${await fragment.text()}`).toBeTruthy();
      await p.evaluate(html => {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const fresh = doc.querySelector('#private-book-review')!;
        document.querySelector('#private-book-review')!.replaceWith(fresh);
        const style = Array.from(doc.querySelectorAll('style')).find(node => node.textContent?.includes('#private-book-review'));
        if (style) document.head.append(style);
        const source = doc.querySelector('script[src*="book-review.js"]')!.getAttribute('src')!;
        const script = document.createElement('script'); script.src = source; document.body.append(script);
      }, await fragment.text());
      await expect(edit).toBeVisible();
      await expect(region.getByRole('button', { name: 'Retry', exact: true })).not.toBeVisible();
    }
    await edit.click();
    const editor = region.getByRole('textbox', { name: 'Review or note', exact: true });
    await expect(editor).toBeFocused();
    const text = `Private ${info.project.name}: <img src=x onerror=alert(1)>\n  Keep Unicode 🦉 and spacing.  `;
    await editor.fill(text);
    const url = `**/api/v1/books/${bookId}/review`;
    await p.route(url, route => route.request().method() === 'PUT'
      ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Temporary save failure' } }) }) : route.continue());
    await region.getByRole('button', { name: 'Save review', exact: true }).click();
    await expect(editor).toHaveValue(text); await expect(region).toContainText('Temporary save failure');
    expect(await note(p)).toBeNull();
    if (!classic) {
      await expect(p.locator('html')).toHaveAttribute('data-theme', info.project.name === 'desktop' ? 'light' : 'dark');
      const axe = await new AxeBuilder({ page: p }).include('[data-testid="book-review"]').withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
      expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
    }
    await editor.scrollIntoViewIfNeeded();
    await p.screenshot({ path: `${captures}/${classic ? 'classic' : 'new'}-save-retry.jpg`, type: 'jpeg', quality: 65 });
    await p.unroute(url);
    await region.getByRole('button', { name: 'Save review', exact: true }).click();
    await expect(region.getByRole('button', { name: 'Edit review', exact: true })).toBeVisible();
    expect((await note(p))?.text).toBe(text);
    await expect(region.locator('img')).toHaveCount(0);
    await region.scrollIntoViewIfNeeded();
    await p.screenshot({ path: `${captures}/${classic ? 'classic' : 'new'}-saved.jpg`, type: 'jpeg', quality: 65 });
    await p.reload(); await expect(region).toContainText(text.trim());
    // Both interfaces read the very same real saved review.
    await p.goto(classic ? `/app/book/${bookId}` : `/book/${bookId}`);
    await expect(p.locator(classic ? '[data-testid="book-review"]' : '#private-book-review')).toContainText(text.trim());
    await p.goto(classic ? `/book/${bookId}` : `/app/book/${bookId}`);
    await region.getByRole('button', { name: 'Edit review', exact: true }).click();
    await editor.fill('Unsaved replacement'); await region.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await note(p))?.text).toBe(text);
    await expect(region.getByRole('button', { name: 'Edit review', exact: true })).toBeFocused();
    await region.getByRole('button', { name: 'Delete review', exact: true }).click();
    const confirm = classic ? p.locator('#private-review-confirm') : p.getByRole('dialog', { name: 'Delete your review?' });
    await expect(confirm.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    if (!classic) {
      await p.keyboard.press('Shift+Tab'); await expect(confirm.getByRole('button', { name: 'Delete review', exact: true })).toBeFocused();
      await p.keyboard.press('Tab'); await expect(confirm.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
      const axe = await new AxeBuilder({ page: p }).include('[role="dialog"]').withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
      expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
      await p.keyboard.press('Escape'); await expect(confirm).not.toBeVisible();
      await expect(region.getByRole('button', { name: 'Delete review', exact: true })).toBeFocused();
      await region.getByRole('button', { name: 'Delete review', exact: true }).click();
    }
    await p.screenshot({ path: `${captures}/${classic ? 'classic' : 'new'}-delete-confirm.jpg`, type: 'jpeg', quality: 65 });
    await confirm.getByRole('button', { name: 'Delete review', exact: true }).click();
    await expect(region.getByRole('button', { name: 'Add a review', exact: true })).toBeVisible();
    await expect(region.getByRole('heading', { name: 'Your review', exact: true })).toBeFocused();
    expect(await note(p)).toBeNull();
    const after = await (await p.request.get(`/api/v1/books/${bookId}`)).json();
    for (const field of ['description_html', 'rating', 'read_status', 'progress']) expect(after[field]).toEqual(original[field]);
    if (!classic) {
      const axe = await new AxeBuilder({ page: p }).include('[data-testid="book-review"]').withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
      expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
    }
    expect(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  });
}

test('review API isolates readers and admins, rejects forgery and retains a single row under concurrent saves', async ({ browser, baseURL }, info) => {
  test.skip(info.project.name !== 'desktop', 'account/API boundary runs once; both UI flows run at both widths');
  const url = `/api/v1/books/${bookId}/review`; const csrf = await token(active);
  const replies = await Promise.all(Array.from({ length: 6 }, (_, n) => active.request.put(url,
    { headers: { 'X-CSRFToken': csrf }, data: { text: `Concurrent ${n}` } })));
  expect(replies.map(r => r.status())).toEqual([200,200,200,200,200,200]);
  expect((await note(active))?.text).toMatch(/^Concurrent [0-5]$/);
  const forged = await active.request.put(url, { headers: { 'X-CSRFToken': csrf, Origin: 'https://cross-site.invalid' }, data: { text: 'forged' } });
  expect(forged.status()).toBe(403);
  const missing = await active.request.put(url, { data: { text: 'missing csrf' } }); expect(missing.status()).toBe(400);
  const other = await browser.newContext({ baseURL });
  try {
    const p = await other.newPage(); const credentials = adminCredentialsFromEnvironment();
    const login = await p.request.post('/api/v1/auth/login', { headers: { 'X-CSRFToken': await token(p) }, data: { ...credentials, remember: false } }); expect(login.ok()).toBeTruthy();
    const response = await p.request.get(url); expect(response.headers()['cache-control']).toBe('private, no-store');
    expect((await response.json()).review).toBeNull();
    const queried = await p.request.get(`${url}?user_id=${owner.user.id}`); expect((await queried.json()).review).toBeNull();
    expect((await note(active))?.text).toMatch(/^Concurrent [0-5]$/);
  } finally { await other.close(); }
});
