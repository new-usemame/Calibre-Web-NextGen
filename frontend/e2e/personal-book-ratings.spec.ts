import AxeBuilder from '@axe-core/playwright';
import { expect, test } from './fixtures';
import type { BrowserContext, Page } from '@playwright/test';
import { createOwnedUser, createOwnedUserIdentity, cleanupOwnedUser } from './user-reaper';
import { DirectAdminApi, adminCredentialsFromEnvironment } from './direct-admin-api';
import { assertNoHorizontalOverflow } from './utils';

test.describe.configure({ mode: 'serial' });
let admin: DirectAdminApi;
let owners: Awaited<ReturnType<typeof createOwnedUser>>[];
let contexts: BrowserContext[];
let pages: Page[];
let bookId: number;
let libraryRating: number | null;
async function token(page: Page) { return (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token as string; }
async function scores(page: Page) {
  const response = await page.request.get(`/api/v1/books/${bookId}/rating`);
  expect(response.ok(), await response.text()).toBeTruthy();
  expect(response.headers()['cache-control']).toBe('private, no-store');
  expect(response.headers()['vary']).toContain('Cookie');
  return response.json() as Promise<{ personal_rating: number | null; household_rating: number | null }>;
}
async function share(page: Page, enabled: boolean, classic: boolean) {
  await page.goto(classic ? '/me' : '/app/account');
  const checkbox = page.getByRole('checkbox', { name: /^Share my ratings in the household average/ });
  await expect(checkbox).toBeEnabled();
  await checkbox.setChecked(enabled);
  if (classic) await page.locator('#user_submit').click();
  await expect.poll(async () => (await (await page.request.get('/api/v1/auth/me')).json()).preferences.share_book_ratings).toBe(enabled);
  await page.goto(classic ? `/book/${bookId}` : `/app/book/${bookId}`);
}


test.beforeEach(async ({ browser, baseURL }, info) => {
  if (!baseURL) throw new Error('Personal ratings require the owned rig');
  owners = []; contexts = []; pages = [];
  admin = await DirectAdminApi.open(baseURL, adminCredentialsFromEnvironment());
  for (let index = 0; index < 2; index++) {
    const { username, email } = createOwnedUserIdentity(`${info.project.name}-rating-${index}`, info.workerIndex);
    const password = `Aa7!${username.slice(-22)}`;
    const owner = await createOwnedUser(admin, baseURL, { name: username, email, password,
      roles: { admin: false, viewer: true, download: false, upload: false, edit: false, edit_shelfs: false, delete_books: false } },
    { runId: String(info.config.metadata.cwngE2ERunId), workerId: `${info.project.name}:${info.workerIndex}:personal-rating:${index}` });
    owners.push(owner);
    const use = info.project.use;
    const context = await browser.newContext({ baseURL, viewport: use.viewport, hasTouch: use.hasTouch,
      isMobile: use.isMobile, userAgent: use.userAgent, deviceScaleFactor: use.deviceScaleFactor });
    contexts.push(context);
    const page = await context.newPage(); pages.push(page);
    const login = await page.request.post('/api/v1/auth/login', { headers: { 'X-CSRFToken': await token(page) }, data: { username, password, remember: false } });
    expect(login.ok(), await login.text()).toBeTruthy();
    const theme = await page.request.post('/api/v1/account/profile', { headers: { 'X-CSRFToken': await token(page) },
      data: { theme: info.project.name.includes('phone') || info.project.name === 'mobile' ? 'dark' : 'light' } });
    expect(theme.ok(), await theme.text()).toBeTruthy();
  }
  const books = await (await pages[0].request.get('/api/v1/books?per_page=2')).json();
  expect(books.items.length).toBeGreaterThan(0); bookId = books.items[0].id;
  libraryRating = (await (await pages[0].request.get(`/api/v1/books/${bookId}`)).json()).rating;
});
test.afterEach(async ({ baseURL }) => {
  try {
    const closed = await Promise.allSettled((contexts ?? []).map(context => context.close()));
    expect(closed.filter(result => result.status === 'rejected'), 'every owned browser context must close').toEqual([]);
  }
  finally {
    try {
      const failures: unknown[] = [];
      if (admin && baseURL) for (const owner of owners ?? []) {
        try { await cleanupOwnedUser(admin, owner.ownership, owner.user.id); }
        catch (error) { failures.push(error); }
      }
      expect(failures, 'every owned account must be deleted').toEqual([]);
    }
    finally { await admin?.dispose(); }
  }
});

for (const classic of [false, true]) {
  test(`${classic ? 'Classic' : 'New UI'} personal rating stays private, survives failure, shares only by consent and clears`, async ({}, info) => {
    const [p, other] = pages;
    await p.goto(classic ? `/book/${bookId}` : `/app/book/${bookId}`);
    const region = classic ? p.locator('#personal-rating') : p.getByRole('region', { name: 'Your rating', exact: true });
    const select = region.getByRole('combobox', { name: 'Your rating', exact: true });
    await expect(select).toBeEnabled();
    if (!classic) await expect(p.locator('html')).toHaveAttribute('data-theme',
      info.project.name.includes('phone') || info.project.name === 'mobile' ? 'dark' : 'light');
    await expect(select).toHaveValue('0');
    const rejected = await p.request.put(`/api/v1/books/${bookId}/rating`, { data: { rating: 10 } });
    expect(rejected.status(), 'rating writes without CSRF must be rejected').toBe(400);
    const absent = await p.request.put('/api/v1/books/2147483000/rating', {
      headers: { 'X-CSRFToken': await token(p) }, data: { rating: 10 },
    });
    expect(absent.status()).toBe(404);
    expect((await scores(p)).personal_rating).toBeNull();
    expect(await scores(other)).toEqual({ personal_rating: null, household_rating: null });
    await select.focus(); await select.selectOption('9');
    const route = `**/api/v1/books/${bookId}/rating`;
    await p.route(route, r => r.request().method() === 'PUT'
      ? r.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Temporary save failure' } }) }) : r.continue());
    await region.getByRole('button', { name: 'Save rating', exact: true }).click();
    await expect(region.getByRole('alert')).toHaveText('Could not save your rating.');
    await expect(select).toHaveValue('9');
    expect((await scores(p)).personal_rating).toBeNull();
    await p.unroute(route);
    await region.getByRole('button', { name: 'Save rating', exact: true }).click();
    await expect.poll(async () => (await scores(p)).personal_rating).toBe(9);
    await p.reload(); await expect(select).toHaveValue('9');
    expect(await scores(other)).toEqual({ personal_rating: null, household_rating: null });
    await share(p, true, classic);
    expect(await scores(other)).toEqual({ personal_rating: null, household_rating: 9 });
    await other.goto(classic ? `/book/${bookId}` : `/app/book/${bookId}`);
    const otherRegion = classic ? other.locator('#personal-rating') : other.getByRole('region', { name: 'Your rating', exact: true });
    await expect(otherRegion).toContainText('Household average');
    await expect(otherRegion.getByRole('combobox', { name: 'Your rating', exact: true })).toHaveValue('0');
    await expect(otherRegion).not.toContainText(owners[0].user.name);
    await share(p, false, classic);
    expect((await scores(other)).household_rating).toBeNull();
    await select.selectOption('10'); await region.getByRole('button', { name: 'Save rating', exact: true }).click();
    await expect.poll(async () => (await scores(p)).personal_rating).toBe(10);
    const rated = await (await p.request.get('/api/v1/books?filter=rated&per_page=1')).json();
    expect(rated.total).toBe(1); expect(rated.items[0].id).toBe(bookId);
    const otherRated = await (await other.request.get('/api/v1/books?filter=rated')).json();
    expect(otherRated.total).toBe(0);
    const filtered = await (await p.request.get('/api/v1/books?personal_rating=10&sort=ratingdesc&per_page=1')).json();
    expect(filtered.total).toBe(1); expect(filtered.items[0].id).toBe(bookId);
    await assertNoHorizontalOverflow(p);
    const axe = await new AxeBuilder({ page: p }).include(classic ? '#personal-rating' : 'section[aria-labelledby="your-rating-heading"]').analyze();
    expect(axe.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
    await p.screenshot({ path: info.outputPath(`personal-rating-${classic ? 'classic' : 'new'}.jpg`), type: 'jpeg', quality: 70 });
    await select.selectOption('0'); await region.getByRole('button', { name: 'Save rating', exact: true }).click();
    await expect.poll(async () => (await scores(p)).personal_rating).toBeNull();
    const after = await (await p.request.get(`/api/v1/books/${bookId}`)).json();
    expect(after.rating).toEqual(libraryRating);
    expect(after.personal_rating).toBeNull();
  });
}
