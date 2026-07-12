import { test, expect } from '@playwright/test';
import { assertNoHorizontalOverflow } from './utils';

test('library density control persists and produces a denser mobile grid', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/app');
  await page.getByRole('button', { name: 'View settings' }).click();
  await page.getByRole('radio', { name: 'Dense' }).check();
  await expect(page.getByRole('radio', { name: 'Dense' })).toBeChecked();
  expect(await page.evaluate(() => localStorage.getItem('cwng:catalog-density-v1'))).toBe('dense');
  await page.reload();
  const cards = page.locator('a[aria-label^="Open details for"]');
  if (await cards.count() >= 4) {
    const tops = await cards.evaluateAll((nodes) => nodes.slice(0, 4).map((node) => node.getBoundingClientRect().top));
    expect(new Set(tops.map(Math.round)).size).toBe(1);
  }
  await assertNoHorizontalOverflow(page);
});

test('series offers the remembered grid/list alternative', async ({ page }) => {
  await page.goto('/app');
  const series = await page.evaluate(async () => {
    const response = await fetch('/api/v1/series');
    return response.ok ? (await response.json()).items?.[0] : null;
  });
  test.skip(!series, 'seed has no series');
  await page.goto(`/app/series/${series.id}`);
  await page.getByRole('button', { name: 'List view' }).click();
  await expect(page.getByRole('button', { name: 'List view' })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => localStorage.getItem('cwng:series-presentation-v1'))).toBe('list');
});

test('book detail exposes imported name, tag disclosure, and semantic progress', async ({ page }) => {
  await page.goto('/app');
  const book = await page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1');
    return response.ok ? (await response.json()).items?.[0] : null;
  });
  test.skip(!book, 'seed has no book');
  await page.route(`**/api/v1/books/${book.id}`, async (route) => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.original_filename = 'reader-selected-name.epub';
    detail.in_progress = true;
    detail.kosync_progress = 42.4;
    detail.tags = Array.from({ length: 10 }, (_, i) => ({ id: i + 1000, name: `SP2 tag ${i + 1}` }));
    await route.fulfill({ response, json: detail });
  });
  await page.goto(`/app/book/${book.id}`);
  await expect(page.getByText('reader-selected-name.epub')).toBeVisible();
  const progress = page.getByRole('progressbar', { name: 'Reading progress' });
  await expect(progress).toHaveAttribute('aria-valuenow', '42');
  const disclosure = page.locator('button[aria-controls="book-tags"]');
  await expect(disclosure).toHaveAccessibleName('Show all 10 tags');
  await expect(page.getByText('SP2 tag 10')).toHaveCount(0);
  await disclosure.click();
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('SP2 tag 10')).toBeVisible();
});

test('Customize panel can restore hidden Table view', async ({ page }) => {
  await page.goto('/app');
  await page.getByRole('button', { name: 'Customize navigation' }).click();
  const table = page.getByRole('checkbox', { name: 'Show Table view' });
  await expect(table).toBeVisible();
  if (!(await table.isChecked())) await table.check();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByRole('link', { name: 'Table view' })).toBeVisible();
});

test('login presents magic link and every configured provider in one named row', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({
    status: 401, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'unauthenticated', message: 'Login required' } }),
  }));
  await page.route('**/api/v1/auth/config', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      instance_name: 'Library', public_registration: false, register_email: false,
      mail_configured: false, standard_login_disabled: false, remote_login: true,
      oauth_providers: [
        { id: 1, name: 'Login with GitHub', url: '/oauth/github' },
        { id: 3, name: 'Continue with Household SSO', url: '/oauth/generic' },
      ],
    }),
  }));
  await page.goto('/app');
  const group = page.getByRole('group', { name: 'Login with' });
  await expect(group.getByRole('link', { name: 'Magic link' })).toBeVisible();
  await expect(group.getByRole('link', { name: 'Login with GitHub' })).toBeVisible();
  await expect(group.getByRole('link', { name: 'Continue with Household SSO' })).toBeVisible();
  await expect(page.getByText('generic', { exact: true })).toHaveCount(0);
});

test('shelf detail action toolbar never widens the body at 375px', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  const { csrf_token } = await csrfResponse.json() as { csrf_token: string };
  const headers = { 'X-CSRFToken': csrf_token };
  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `sp2-overflow-${Date.now()}` },
  });
  expect(created.ok(), 'shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;
  try {
    await page.goto(`/app/shelf/${shelfId}`);
    await expect(page.locator('h1')).toBeVisible();
    const widths = await page.evaluate(() => ({ scroll: document.body.scrollWidth, client: document.body.clientWidth }));
    expect(widths.scroll, 'body.scrollWidth must not exceed body.clientWidth at 375px').toBeLessThanOrEqual(widths.client);
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
