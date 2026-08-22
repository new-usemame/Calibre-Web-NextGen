import { test, expect, Page } from '@playwright/test';
import { assertNoHorizontalOverflow, collectPageErrors, assertNoPageErrors } from './utils';

test.describe.configure({ mode: 'serial' });

// Hiding is PERSISTENT per-user state on the server, and these specs hide
// books. Without a restore, a run that fails part-way — or is interrupted —
// leaves a book hidden forever, and from then on every spec that counts books
// sees a library one short. That is not hypothetical: it is exactly how these
// specs were failing (`Expected: 41, Received: 42`), and the poisoned instance
// then mis-attributes the failure to whichever spec happens to count next.
//
// afterAll, not afterEach: this file is `mode: 'serial'`, so later tests build
// on state earlier ones establish — unhiding between them breaks the chain.
// afterAll still runs when a test fails, which is the case that matters.
test.afterAll(async ({ browser }) => {
  const page = await browser.newPage();
  try {
    const csrf = await page.request.get('/api/v1/auth/csrf')
      .then((r) => r.json() as Promise<{ csrf_token: string }>)
      .then((b) => b.csrf_token);
    const visible = await page.request.get('/api/v1/books?per_page=500')
      .then((r) => r.json() as Promise<{ items: Array<{ id: number }> }>);
    const all = await page.request.get('/api/v1/books?per_page=500&show_hidden=1')
      .then((r) => r.json() as Promise<{ items: Array<{ id: number }> }>);
    const shown = new Set(visible.items.map((b) => b.id));
    for (const book of all.items) {
      if (shown.has(book.id)) continue;
      await page.request.post(`/api/v1/books/${book.id}/hidden`, {
        headers: { 'X-CSRFToken': csrf },
        data: { hidden: false },
      });
    }
  } catch {
    /* best effort — never fail the run in cleanup, only avoid poisoning the next */
  } finally {
    await page.close();
  }
});

async function firstBook(page: Page): Promise<{ id: number; title: string } | null> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1');
    return response.ok ? (await response.json()).items?.[0] ?? null : null;
  });
}

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const response = await page.request.get('/api/v1/auth/csrf');
  const body = await response.json() as { csrf_token: string };
  return { 'X-CSRFToken': body.csrf_token };
}

test('Hide persists across reload; Show hidden reveals a marked book and provides Unhide', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');
  const errors = collectPageErrors(page);

  await page.goto(`/app/book/${book!.id}`);
  const hide = page.getByTestId('hide-book-toggle');
  await expect(hide).toBeVisible();
  await expect(hide).toHaveRole('button');
  const hideName = await hide.getAttribute('aria-label');
  expect(hideName).toBeTruthy();
  const actionControls = page.getByTestId('book-actions').locator('a, button');
  expect(await actionControls.count()).toBeGreaterThan(0);
  for (const control of await actionControls.all()) {
    await expect(control).toHaveAccessibleName(/\S/);
  }

  try {
    const before = await page.request.get('/api/v1/books?per_page=60').then((r) => r.json());
    await hide.click();
    await expect(hide).not.toHaveAttribute('aria-label', hideName!);

    await page.goto('/app');
    const afterHide = await page.request.get('/api/v1/books?per_page=60').then((r) => r.json());
    expect(afterHide.total).toBe(before.total - 1);
    await expect(page.getByRole('link', { name: `Open details for ${book!.title}` })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('link', { name: `Open details for ${book!.title}` })).toHaveCount(0);

    await page.getByTestId('catalog-view-settings').click();
    const showHidden = page.getByTestId('show-hidden-books');
    await expect(showHidden).not.toBeChecked();
    await showHidden.check();
    expect(await page.evaluate(() => localStorage.getItem('cwng_show_hidden_books_v1'))).toBe('1');
    const revealed = await page.request.get('/api/v1/books?per_page=60&show_hidden=1').then((r) => r.json());
    expect(revealed.total).toBe(before.total);

    const card = page.getByRole('link', { name: `Open details for ${book!.title}` });
    await expect(card).toBeVisible();
    const badge = card.getByTestId('hidden-book-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveRole('img');
    expect(await badge.getAttribute('aria-label')).toBeTruthy();

    await page.reload();
    await expect(page.getByRole('link', { name: `Open details for ${book!.title}` })).toBeVisible();
    await page.getByRole('link', { name: `Open details for ${book!.title}` }).click();
    const unhide = page.getByTestId('hide-book-toggle');
    await unhide.click();
    await expect(unhide).toHaveAttribute('aria-label', hideName!);
  } finally {
    // Idempotent cleanup: only toggle when the detail payload still says hidden.
    const detail = await page.request.get(`/api/v1/books/${book!.id}`).then((r) => r.json()).catch(() => null);
    if (detail?.hidden) {
      await page.request.post(`/api/v1/books/${book!.id}/hidden`, {
        headers: await csrfHeaders(page), data: { hidden: false },
      });
    }
    await page.evaluate(() => localStorage.removeItem('cwng_show_hidden_books_v1'));
  }

  assertNoPageErrors(errors);
});

test('hidden+archived remains recoverable through Show hidden, while Archived keeps hidden out', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');

  await page.goto(`/app/book/${book!.id}`);
  try {
    const archive = page.getByTestId('archive-book-toggle');
    const archiveName = await archive.getAttribute('aria-label');
    await archive.click();
    await expect(archive).not.toHaveAttribute('aria-label', archiveName!);
    await page.getByTestId('hide-book-toggle').click();

    await page.goto('/app/archived');
    await expect(page.getByRole('link', { name: `Open details for ${book!.title}` })).toHaveCount(0);

    await page.goto('/app');
    await page.getByTestId('catalog-view-settings').click();
    await page.getByTestId('show-hidden-books').check();
    const card = page.getByRole('link', { name: `Open details for ${book!.title}` });
    await expect(card).toBeVisible();
    await expect(card.getByTestId('hidden-book-badge')).toBeVisible();
  } finally {
    const detail = await page.request.get(`/api/v1/books/${book!.id}`).then((r) => r.json()).catch(() => null);
    const headers = await csrfHeaders(page);
    if (detail?.hidden) {
      await page.request.post(`/api/v1/books/${book!.id}/hidden`, {
        headers, data: { hidden: false },
      });
    }
    if (detail?.archived) await page.request.post(`/api/v1/books/${book!.id}/archived`, { headers });
    await page.evaluate(() => localStorage.removeItem('cwng_show_hidden_books_v1'));
  }
});

test('hiding is per-user and a non-delete user still receives Hide', async ({ page, playwright, baseURL }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');
  const headers = await csrfHeaders(page);
  const username = `hidden-e2e-${Date.now()}`;
  const password = 'CWNG-hidden-E2E-42!';
  const created = await page.request.post('/api/v1/admin/users', {
    headers,
    data: {
      name: username,
      email: `${username}@example.test`,
      password,
      roles: { viewer: true, download: true, delete_books: false },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const other = await playwright.request.newContext({ baseURL });

  try {
    await page.goto(`/app/book/${book!.id}`);
    await page.getByTestId('hide-book-toggle').click();

    const otherCsrf = await other.get('/api/v1/auth/csrf').then((r) => r.json());
    const login = await other.post('/api/v1/auth/login', {
      headers: { 'X-CSRFToken': otherCsrf.csrf_token },
      data: { username, password },
    });
    expect(login.ok(), await login.text()).toBe(true);
    const me = await other.get('/api/v1/auth/me').then((r) => r.json());
    expect(me.role.delete_books).toBe(false);
    const otherBooks = await other.get('/api/v1/books?per_page=60').then((r) => r.json());
    expect(otherBooks.items.some((item: { id: number }) => item.id === book!.id)).toBe(true);
    const otherDetail = await other.get(`/api/v1/books/${book!.id}`).then((r) => r.json());
    expect(otherDetail.hidden).toBe(false);

    // UI role gate: Hide is personal and must not inherit the destructive
    // delete-books permission. Isolation above used two real server sessions;
    // this interception changes only the current page's role presentation.
    await page.route('**/api/v1/auth/me', async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      payload.role = { ...(payload.role ?? {}), delete_books: false };
      await route.fulfill({ response, json: payload });
    });
    await page.reload();
    const personalAction = page.getByTestId('hide-book-toggle');
    await expect(personalAction).toBeVisible();
    expect(await personalAction.evaluate((node) => node.nextElementSibling === null)).toBe(true);
  } finally {
    const cleanup = await page.request.post(`/api/v1/books/${book!.id}/hidden`, {
      headers, data: { hidden: false },
    });
    expect(cleanup.ok()).toBe(true);
    await other.dispose();
  }
});

test('two stale tabs requesting Hide converge on hidden state', async ({ page, browser, baseURL }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');
  const second = await browser.newContext({
    baseURL,
    storageState: await page.context().storageState(),
  });
  const secondPage = await second.newPage();
  const headers = await csrfHeaders(page);
  try {
    const responses = await Promise.all([
      page.request.post(`/api/v1/books/${book!.id}/hidden`, { headers, data: { hidden: true } }),
      secondPage.request.post(`/api/v1/books/${book!.id}/hidden`, {
        headers: await csrfHeaders(secondPage), data: { hidden: true },
      }),
    ]);
    expect(responses.every((response) => response.ok())).toBe(true);
    const detail = await page.request.get(`/api/v1/books/${book!.id}`).then((r) => r.json());
    expect(detail.hidden).toBe(true);
  } finally {
    await page.request.post(`/api/v1/books/${book!.id}/hidden`, {
      headers, data: { hidden: false },
    });
    await second.close();
  }
});

test('Guest never receives a Hide action even when the instance feature is enabled', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.role = { ...(me.role ?? {}), anonymous: true, delete_books: false };
    me.features = { ...(me.features ?? {}), hide_books: true };
    await route.fulfill({ response, json: me });
  });
  await page.goto(`/app/book/${book!.id}`);
  await expect(page.getByTestId('hide-book-toggle')).toHaveCount(0);
});

test('mobile detail actions and View settings stay within 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(!book, 'seed has no books');
  await page.getByTestId('catalog-view-settings').click();
  await expect(page.getByTestId('show-hidden-books')).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await page.goto(`/app/book/${book!.id}`);
  await expect(page.getByTestId('hide-book-toggle')).toBeVisible();
  await assertNoHorizontalOverflow(page);
});
