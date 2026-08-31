import { test, expect } from './fixtures';

async function csrfHeaders(page: import('@playwright/test').Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  const payload = await response.json() as { csrf_token: string };
  return { 'X-CSRFToken': payload.csrf_token };
}

async function installReadNowObserver(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    const observed = window as typeof window & { __readNowMounted?: boolean };
    const start = () => {
      const mark = () => {
        if ([...document.querySelectorAll('a')]
          .some((link) => link.textContent?.trim() === 'Read now')) {
          observed.__readNowMounted = true;
        }
      };
      mark();
      new MutationObserver(mark).observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  });
}

async function expectCardActionsAvailable(page: import('@playwright/test').Page) {
  if (test.info().project.use.hasTouch === true) {
    const card = page.locator('[class*="wrap"]').filter({
      has: page.locator('a[aria-label^="Read "]'),
    }).first();
    const more = card.getByRole('button', { name: /^More actions for / });
    await expect(more).toBeVisible();
    await more.click();
    await expect(card
      .getByRole('group', { name: /^Actions for / })
      .getByRole('link', { name: /^Read / })).toBeVisible();
    await page.keyboard.press('Escape');
    return;
  }

  await expect(page.getByText('Read now', { exact: true }).first()).toBeVisible();
}

function preferenceWrite(
  response: import('@playwright/test').Response,
  name: string,
  value?: boolean,
) {
  if (!response.url().includes('/api/v1/account/preferences')
      || response.request().method() !== 'POST') return false;
  const body = response.request().postDataJSON() as {
    preferences?: Record<string, boolean>;
  };
  return Object.prototype.hasOwnProperty.call(body.preferences ?? {}, name)
    && (value === undefined || body.preferences?.[name] === value);
}

test('Discover adopts local hidden state once and follows the account across browsers', async ({
  secondaryUser, browser, baseURL,
}) => {
  const { page, context, username, password } = secondaryUser;
  await expect(page.getByTestId('discover-section')).toBeVisible();

  // The observer starts before React. If Discover ever mounts before /me's
  // server/local preference decision, this catches the visible-then-hide flash.
  await page.addInitScript(() => {
    const windowWithFlag = window as typeof window & { __discoverMounted?: boolean };
    const start = () => {
      const mark = () => {
        if (document.querySelector('[data-testid="discover-section"]')) {
          windowWithFlag.__discoverMounted = true;
        }
      };
      mark();
      new MutationObserver(mark).observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  });
  await page.evaluate(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));

  const adoption = page.waitForResponse((response) =>
    preferenceWrite(response, 'discover_hidden', true));
  await page.reload();
  expect((await adoption).ok()).toBeTruthy();
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  expect(await page.evaluate(() =>
    (window as typeof window & { __discoverMounted?: boolean }).__discoverMounted ?? false,
  )).toBe(false);

  await expect.poll(async () => {
    const adoptedMe = await page.request.get('/api/v1/auth/me');
    return (await adoptedMe.json() as {
      preferences: { discover_hidden: boolean | null };
    }).preferences.discover_hidden;
  }).toBe(true);

  // Browser B gets only the same account cookies, never browser A's storage.
  const browserB = await browser.newContext({ baseURL });
  try {
    await browserB.addCookies(await context.cookies());
    const pageB = await browserB.newPage();
    await pageB.goto('/app');
    await expect(pageB.getByTestId('discover-section')).toHaveCount(0);
    await expect.poll(() => pageB.evaluate(() =>
      localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');

    // The gear checkbox writes false and makes the section visible.
    await pageB.getByTestId('catalog-view-settings').click();
    const showDiscover = pageB.getByTestId('show-discover-section');
    await expect(showDiscover).not.toBeChecked();
    const showSaved = pageB.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await showDiscover.click();
    expect((await showSaved).ok()).toBeTruthy();
    await expect(showDiscover).toBeChecked();
    await expect(pageB.getByTestId('discover-section')).toBeVisible();

    // Browser A sees Browser B's choice after local storage is removed.
    await page.evaluate(() => localStorage.removeItem('cwng_discover_hidden_v1'));
    await page.reload();
    await expect(page.getByTestId('discover-section')).toBeVisible();

    // The section's × writes through too.
    const hideSaved = page.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Hide Discover section' }).click();
    expect((await hideSaved).ok()).toBeTruthy();
    await expect(page.getByTestId('discover-section')).toHaveCount(0);

    // Logout, clear local storage, log the same account back in: server state wins.
    const logout = await browserB.request.post('/api/v1/auth/logout', {
      headers: await csrfHeaders(pageB),
    });
    expect(logout.status()).toBe(204);
    await pageB.goto('/app');
    await pageB.evaluate(() => localStorage.removeItem('cwng_discover_hidden_v1'));
    const login = await browserB.request.post('/api/v1/auth/login', {
      headers: await csrfHeaders(pageB),
      data: { username, password, remember: false },
    });
    expect(login.ok(), await login.text()).toBeTruthy();
    await pageB.goto('/app');
    await expect(pageB.getByTestId('discover-section')).toHaveCount(0);
    await expect.poll(() => pageB.evaluate(() =>
      localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');
  } finally {
    await browserB.close();
  }
});

test('hidden books and card actions adopt local state and follow the account', async ({
  secondaryUser, browser, baseURL,
}) => {
  const { page, context } = secondaryUser;
  await expectCardActionsAvailable(page);

  const adopted = new Set<string>();
  page.on('request', (request) => {
    if (request.method() !== 'POST'
        || !request.url().includes('/api/v1/account/preferences')) return;
    const payload = request.postDataJSON() as { preferences?: Record<string, boolean> };
    for (const name of Object.keys(payload.preferences ?? {})) adopted.add(name);
  });
  await installReadNowObserver(page);
  await page.evaluate(() => {
    localStorage.setItem('cwng_show_hidden_books_v1', '1');
    localStorage.setItem('cwng:card-actions-hidden-v1', '1');
  });
  await page.reload();

  await expect.poll(() => [...adopted].sort()).toEqual([
    'card_actions_hidden', 'show_hidden_books',
  ]);
  await expect(page.getByText('Read now', { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() =>
    (window as typeof window & { __readNowMounted?: boolean }).__readNowMounted ?? false,
  )).toBe(false);

  await page.getByTestId('catalog-view-settings').click();
  await expect(page.getByTestId('show-hidden-books')).toBeChecked();
  await expect(page.getByTestId('show-card-actions')).not.toBeChecked();
  const adoptedMe = await page.request.get('/api/v1/auth/me').then((r) => r.json()) as {
    preferences: Record<string, boolean | null>;
  };
  expect(adoptedMe.preferences.show_hidden_books).toBe(true);
  expect(adoptedMe.preferences.card_actions_hidden).toBe(true);

  const browserB = await browser.newContext({ baseURL });
  try {
    await browserB.addCookies(await context.cookies());
    const pageB = await browserB.newPage();
    await installReadNowObserver(pageB);
    await pageB.goto('/app');
    await pageB.getByTestId('catalog-view-settings').click();
    const showHidden = pageB.getByTestId('show-hidden-books');
    const showCardActions = pageB.getByTestId('show-card-actions');
    await expect(showHidden).toBeChecked();
    await expect(showCardActions).not.toBeChecked();
    await expect(pageB.getByText('Read now', { exact: true })).toHaveCount(0);
    expect(await pageB.evaluate(() =>
      (window as typeof window & { __readNowMounted?: boolean }).__readNowMounted ?? false,
    )).toBe(false);
    await expect.poll(() => pageB.evaluate(() => ({
      showHidden: localStorage.getItem('cwng_show_hidden_books_v1'),
      cardActions: localStorage.getItem('cwng:card-actions-hidden-v1'),
    }))).toEqual({ showHidden: '1', cardActions: '1' });

    const hiddenSaved = pageB.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await showHidden.click();
    expect((await hiddenSaved).ok()).toBeTruthy();
    await expect(showHidden).not.toBeChecked();

    const actionsSaved = pageB.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await showCardActions.click();
    expect((await actionsSaved).ok()).toBeTruthy();
    await expect(showCardActions).toBeChecked();
    await pageB.keyboard.press('Escape');
    await expectCardActionsAvailable(pageB);

    await page.evaluate(() => {
      localStorage.removeItem('cwng_show_hidden_books_v1');
      localStorage.removeItem('cwng:card-actions-hidden-v1');
    });
    await page.reload();
    await page.getByTestId('catalog-view-settings').click();
    await expect(page.getByTestId('show-hidden-books')).not.toBeChecked();
    await expect(page.getByTestId('show-card-actions')).toBeChecked();
  } finally {
    await browserB.close();
  }
});

test('named preference writes are optimistic and roll back on failure', async ({
  secondaryUser,
}) => {
  const { page } = secondaryUser;
  let requestCount = 0;
  let markFirstStarted!: () => void;
  let markSecondStarted!: () => void;
  let releaseFirst!: () => void;
  let releaseSecond!: () => void;
  const firstStarted = new Promise<void>((resolve) => { markFirstStarted = resolve; });
  const secondStarted = new Promise<void>((resolve) => { markSecondStarted = resolve; });
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const secondGate = new Promise<void>((resolve) => { releaseSecond = resolve; });

  await page.route('**/api/v1/account/preferences', async (route) => {
    const payload = route.request().postDataJSON() as {
      preferences?: Record<string, boolean>;
    };
    if (!Object.prototype.hasOwnProperty.call(
      payload.preferences ?? {}, 'discover_hidden')) {
      await route.continue();
      return;
    }
    requestCount += 1;
    if (requestCount === 1) {
      markFirstStarted();
      await firstGate;
      await route.continue();
      return;
    }
    markSecondStarted();
    await secondGate;
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ error: { message: 'forced failure' } }),
    });
  });

  await page.goto('/app');
  await expect(page.getByTestId('discover-section')).toBeVisible();
  await page.getByTestId('catalog-view-settings').click();
  let showDiscover = page.getByTestId('show-discover-section');

  // The section and local fallback update before the held server request ends.
  const firstSaved = page.waitForResponse((response) =>
    response.url().includes('/api/v1/account/preferences')
    && response.request().method() === 'POST');
  await showDiscover.uncheck();
  await firstStarted;
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  expect(await page.evaluate(() =>
    localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');
  releaseFirst();
  expect((await firstSaved).ok()).toBeTruthy();
  await expect(showDiscover).toBeEnabled();
  await expect(showDiscover).not.toBeChecked();

  // A failed inverse write is optimistic too, then both query cache and local
  // fallback return to the last server-confirmed value with an announced error.
  await page.reload();
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  await page.getByTestId('catalog-view-settings').click();
  showDiscover = page.getByTestId('show-discover-section');
  await expect(showDiscover).not.toBeChecked();
  await showDiscover.click();
  await secondStarted;
  await expect(page.getByTestId('discover-section')).toBeVisible();
  expect(await page.evaluate(() =>
    localStorage.getItem('cwng_discover_hidden_v1'))).toBe('0');
  releaseSecond();
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');
  await expect(page.getByText('Could not save.', { exact: true })).toBeVisible();
});

test('guest catalog preferences stay local and never post', async ({ page }) => {
  let preferencePosts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/account/preferences')) {
      preferencePosts += 1;
    }
  });
  await page.addInitScript(() => {
    localStorage.setItem('cwng_discover_hidden_v1', '1');
    localStorage.setItem('cwng_show_hidden_books_v1', '1');
    localStorage.setItem('cwng:card-actions-hidden-v1', '1');
  });
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.role = { ...(me.role ?? {}), anonymous: true };
    me.preferences = {
      discover_hidden: null,
      show_hidden_books: null,
      card_actions_hidden: null,
    };
    await route.fulfill({ response, json: me });
  });

  const hiddenBooksRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === '/api/v1/books' && url.searchParams.get('show_hidden') === '1';
  });
  await page.goto('/app');
  await hiddenBooksRequest;
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  await page.getByTestId('catalog-view-settings').click();
  // Guests keep the existing catalog UI: hidden-book visibility is honored
  // from storage, but the account-only checkbox is not offered.
  await expect(page.getByTestId('show-hidden-books')).toHaveCount(0);
  await expect(page.getByTestId('show-card-actions')).not.toBeChecked();
  await page.getByTestId('show-discover-section').check();
  await page.getByTestId('show-card-actions').check();
  await expect(page.getByTestId('discover-section')).toBeVisible();
  expect(await page.evaluate(() => ({
    discover: localStorage.getItem('cwng_discover_hidden_v1'),
    showHidden: localStorage.getItem('cwng_show_hidden_books_v1'),
    cardActions: localStorage.getItem('cwng:card-actions-hidden-v1'),
  }))).toEqual({ discover: '0', showHidden: '1', cardActions: '0' });
  await expect.poll(() => preferencePosts).toBe(0);
});
