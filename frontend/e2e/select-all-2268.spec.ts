import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

type Book = { id: number; title: string };

async function csrf(page: import('@playwright/test').Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return { 'X-CSRFToken': ((await response.json()) as { csrf_token: string }).csrf_token };
}

async function selectAllOn(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  const all = page.getByRole('button', { name: /^Select all \d+ books$/ });
  await expect(all).toBeEnabled();
  await all.click();
}

test('library select-all uses complete server result and bulk changes books beyond the loaded page', async ({ secondaryUser }, testInfo) => {
  const page = secondaryUser.page;
  const viewport = testInfo.project.name === 'mobile'
    ? { width: 375, height: 812 }
    : { width: 1280, height: 800 };
  const headers = await csrf(page);
  const themeResponse = await page.request.post('/api/v1/account/profile', { headers, data: { theme: 'light' } });
  expect(themeResponse.ok(), await themeResponse.text()).toBeTruthy();
  const visibleResponse = await page.request.get('/api/v1/books?per_page=500&sort=new');
  expect(visibleResponse.ok(), await visibleResponse.text()).toBeTruthy();
  const visible = ((await visibleResponse.json()) as { items: Book[] }).items;
  expect(visible.length, 'isolated library seed must span several pages').toBeGreaterThan(6);

  // Hidden state belongs only to this short-lived test account. It proves the
  // selection endpoint uses the same visible-library policy as the cards.
  const hiddenBook = visible[0];
  const hiddenResponse = await page.request.post(`/api/v1/books/${hiddenBook.id}/hidden`, {
    headers, data: { hidden: true },
  });
  expect(hiddenResponse.ok(), await hiddenResponse.text()).toBeTruthy();
  const expectedResponse = await page.request.get('/api/v1/books?select_all=1&sort=new');
  expect(expectedResponse.ok(), await expectedResponse.text()).toBeTruthy();
  const expected = (await expectedResponse.json()) as { ids: number[]; total: number };
  expect(expected.total).toBe(visible.length - 1);
  expect(expected.ids).not.toContain(hiddenBook.id);

  // Keep the catalog on the real API but bound ordinary card pages to three;
  // select_all requests still go unchanged to the server's full-view query.
  let ordinaryPageItems = 0;
  let heldLaterPage = false;
  let releaseLaterPages!: () => void;
  const laterPages = new Promise<void>(resolve => { releaseLaterPages = resolve; });
  await page.route('**/api/v1/books?*', async route => {
    const url = new URL(route.request().url());
    if (!url.searchParams.has('select_all')) {
      // Infinite scrolling can auto-fill a small CI seed before selection.
      // Hold later real page responses until this scenario has proved that
      // bulk selection reaches beyond the first loaded page.
      if (Number(url.searchParams.get('page') || '1') > 1) {
        heldLaterPage = true;
        await laterPages;
      }
      url.searchParams.set('per_page', '3');
      const response = await route.fetch({ url: url.toString() });
      const body = await response.json().catch(() => null) as { items?: unknown[] } | null;
      if (body?.items) ordinaryPageItems = Math.max(ordinaryPageItems, body.items.length);
      await route.fulfill({ response, body: body ? JSON.stringify(body) : undefined });
    } else {
      await route.continue();
    }
  });
  try {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.goto('/app');
    await expect(page.getByRole('heading', { name: 'Your Library' })).toBeVisible();
    await expect(page.getByTestId('catalog-grid').getByRole('link', {
      name: `Open details for ${visible[1].title}`,
    })).toBeVisible();
    await expect.poll(() => page.getByTestId('catalog-grid').getByRole('link', { name: /^Open details for / }).count()).toBeLessThan(expected.total);
    // Reach the slow next-page state through the actual paging control. The
    // complete-ID selection must work even while that card request is held.
    await page.getByRole('button', { name: 'Load more', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => heldLaterPage).toBe(true);
    await selectAllOn(page);
    const selected = page.getByRole('region', { name: `${expected.total} selected`, exact: true });
    await expect(selected).toBeVisible();
    expect(ordinaryPageItems).toBeLessThan(expected.total);
    await page.screenshot({
      path: testInfo.outputPath(`library-select-all-${viewport.width}-selected.jpg`),
      type: 'jpeg',
      quality: 75,
    });
    const axe = await new AxeBuilder({ page }).include('main').analyze();
    expect(axe.violations.filter(issue => ['critical', 'serious'].includes(issue.impact ?? ''))).toEqual([]);

    // Mark read is a real bulk mutation. The resulting server query must include
    // every selected ID, including records that never appeared in the 3-card page.
    let releaseMutation!: () => void;
    const mutationGate = new Promise<void>(resolve => { releaseMutation = resolve; });
    let failOneMutation = true;
    await page.route('**/api/v1/books/*/read', async route => {
      await mutationGate;
      const url = new URL(route.request().url());
      if (failOneMutation && url.pathname === `/api/v1/books/${visible[1].id}/read`) {
        failOneMutation = false;
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({
          error: 'injected_read_failure', message: 'Injected one-book read failure.',
        }) });
      } else {
        await route.continue();
      }
    });
    await selected.getByRole('button', { name: 'Mark read', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: `Deselect ${visible[1].title}`, exact: true })).toBeDisabled();
    releaseMutation();
    await expect(page.getByText(/The failed books remain selected/)).toBeVisible();
    const retrySelection = page.getByRole('region', { name: '1 selected', exact: true });
    await expect(retrySelection).toBeVisible();
    await expect(page.getByRole('button', {
      name: `Deselect ${visible[1].title}`, exact: true,
    })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`library-select-all-${viewport.width}-retryable-failure.jpg`),
      type: 'jpeg',
      quality: 75,
    });
    // The mutation failure was intercepted for one selected book. Retry it
    // through the real API and confirm the full select-all result is now read.
    await page.unroute('**/api/v1/books/*/read');
    await retrySelection.getByRole('button', { name: 'Mark read', exact: true }).click();
    await expect(page.locator('[aria-live="polite"]')).toContainText('1 marked as read.');
    const readResponse = await page.request.get('/api/v1/books?filter=read&select_all=1&sort=new');
    expect(readResponse.ok(), await readResponse.text()).toBeTruthy();
    const readIds = (await readResponse.json() as { ids: number[] }).ids;
    for (const id of expected.ids) expect(readIds).toContain(id);
    await page.screenshot({
      path: testInfo.outputPath(`library-select-all-${viewport.width}-retry-complete.jpg`),
      type: 'jpeg',
      quality: 75,
    });
  } finally {
    releaseLaterPages();
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('library search select-all uses the current server-matched query', async ({ secondaryUser }, testInfo) => {
  const page = secondaryUser.page;
  if (testInfo.project.name === 'mobile') await page.setViewportSize({ width: 375, height: 667 });
  const listing = await page.request.get('/api/v1/books?per_page=20&sort=abc');
  expect(listing.ok(), await listing.text()).toBeTruthy();
  const books = ((await listing.json()) as { items: Book[] }).items;
  expect(books.length).toBeGreaterThan(0);
  const term = books[0].title.trim().split(/\s+/)[0];
  const query = new URLSearchParams({ search: term, select_all: '1', sort: 'new' });
  const expectedResponse = await page.request.get(`/api/v1/books?${query.toString()}`);
  expect(expectedResponse.ok(), await expectedResponse.text()).toBeTruthy();
  const expected = (await expectedResponse.json()) as { ids: number[]; total: number };
  expect(expected.total).toBeGreaterThan(0);

  await page.goto(`/app?q=${encodeURIComponent(term)}`);
  const openSearch = page.getByRole('button', { name: 'Search the library', exact: true });
  if (await openSearch.isVisible().catch(() => false)) {
    await openSearch.click();
    await expect(openSearch).toHaveAttribute('aria-expanded', 'true');
  }
  await expect(page.getByText(`${expected.total} results for "${term}"`, { exact: true })).toBeVisible();
  await selectAllOn(page);
  await expect(page.getByRole('region', { name: `${expected.total} selected`, exact: true })).toBeVisible();
});

test('manual and smart shelf select-all match each shelf’s complete server result', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const headers = await csrf(page);
  const themeResponse = await page.request.post('/api/v1/account/profile', { headers, data: { theme: 'dark' } });
  expect(themeResponse.ok(), await themeResponse.text()).toBeTruthy();
  const library = await page.request.get('/api/v1/books?per_page=20&sort=abc');
  expect(library.ok(), await library.text()).toBeTruthy();
  const books = ((await library.json()) as { items: Book[] }).items;
  expect(books.length).toBeGreaterThanOrEqual(4);
  const selectedBooks = books.slice(0, 4);

  const manual = await page.request.post('/api/v1/shelves', {
    headers, data: { name: `2268 manual ${secondaryUser.username}` },
  });
  expect(manual.ok(), await manual.text()).toBeTruthy();
  const manualId = (await manual.json() as { id: number }).id;
  for (const book of selectedBooks) {
    const response = await page.request.post(`/api/v1/shelves/${manualId}/books/${book.id}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  const smart = await page.request.post('/magicshelf', {
    headers, data: {
      name: `2268 smart ${secondaryUser.username}`, icon: '📚',
      rules: { condition: 'OR', rules: selectedBooks.map(book => ({
        id: 'title', operator: 'equal', value: book.title,
      })) },
    },
  });
  expect(smart.ok(), await smart.text()).toBeTruthy();
  const smartId = (await smart.json() as { shelf_id: number }).shelf_id;

  for (const [kind, id] of [['shelf', manualId], ['magic', smartId]] as const) {
    await page.route(`**/api/v1/${kind === 'shelf' ? 'shelves' : 'magicshelf'}/${id}?*`, async route => {
      const url = new URL(route.request().url());
      if (!url.searchParams.has('select_all')) url.searchParams.set('per_page', '2');
      await route.continue({ url: url.toString() });
    });
    const endpoint = kind === 'shelf' ? `/api/v1/shelves/${id}?select_all=1&sort=abc`
      : `/api/v1/magicshelf/${id}?select_all=1&sort=abc`;
    const allResponse = await page.request.get(endpoint);
    expect(allResponse.ok(), await allResponse.text()).toBeTruthy();
    const expected = (await allResponse.json() as { ids: number[]; total: number });
    expect(expected.total).toBe(selectedBooks.length);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await page.goto(`/app/${kind}/${id}`);
    await selectAllOn(page);
    await expect(page.getByRole('region', { name: `${expected.total} selected`, exact: true })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath(`${kind}-select-all-desktop-dark.png`) });
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    await page.unroute(`**/api/v1/${kind === 'shelf' ? 'shelves' : 'magicshelf'}/${id}?*`);
  }
});

test('switching shelves during selection retrieval cannot select the previous shelf in the new view', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const headers = await csrf(page);
  const booksResponse = await page.request.get('/api/v1/books?per_page=1&sort=abc');
  const book = ((await booksResponse.json()) as { items: Book[] }).items[0];
  expect(book).toBeTruthy();
  const shelfIds: Array<{ id: number; name: string }> = [];
  for (const label of ['old', 'new']) {
    const name = `2268 switch ${label} ${secondaryUser.username}`;
    const response = await page.request.post('/api/v1/shelves', { headers, data: { name } });
    expect(response.ok(), await response.text()).toBeTruthy();
    const { id } = await response.json() as { id: number };
    shelfIds.push({ id, name });
    const added = await page.request.post(`/api/v1/shelves/${id}/books/${book.id}`, { headers });
    expect(added.ok(), await added.text()).toBeTruthy();
  }

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/app/shelf/${shelfIds[0].id}`);
  await expect(page.getByRole('heading', { name: shelfIds[0].name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/v1/shelves/${shelfIds[0].id}?**select_all=1**`, async route => {
    await gate;
    await route.continue();
  });
  const request = page.waitForRequest(request => request.url().includes(`/shelves/${shelfIds[0].id}?`) && request.url().includes('select_all=1'));
  const response = page.waitForResponse(response => response.url().includes(`/shelves/${shelfIds[0].id}?`) && response.url().includes('select_all=1'));
  await page.getByRole('button', { name: /^Select all \d+ books$/ }).click();
  await request;
  const openNavigation = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await openNavigation.isVisible().catch(() => false)) await openNavigation.click();
  const newShelfLink = page.locator(`nav a[href="/app/shelf/${shelfIds[1].id}"]`).first();
  await expect(newShelfLink).toBeVisible();
  await newShelfLink.click();
  await expect(page.getByRole('heading', { name: shelfIds[1].name, exact: true })).toBeVisible();
  release();
  expect((await response).ok()).toBeTruthy();
  await expect(page.getByRole('button', { name: 'Select', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByRole('region', { name: /selected/ })).toHaveCount(0);
});

test('manual and smart shelf navigation returns to page one after viewing later pages', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const headers = await csrf(page);
  const booksResponse = await page.request.get('/api/v1/books?per_page=4&sort=abc');
  expect(booksResponse.ok(), await booksResponse.text()).toBeTruthy();
  const books = ((await booksResponse.json()) as { items: Book[] }).items;
  expect(books.length, 'isolated shelf fixture needs four distinct books').toBeGreaterThanOrEqual(4);
  const olderBooks = books.slice(0, 3);
  const newBook = books[3];

  const endpoints: Array<{ kind: 'shelf' | 'magic'; id: number; name: string; firstBook: Book; previous: Book[] }> = [];
  const createdManualIds: number[] = [];
  const createdMagicIds: number[] = [];
  try {
    const manualNames = {
      old: `2268 later manual ${secondaryUser.username}`,
      next: `2268 first manual ${secondaryUser.username}`,
    };
    const oldManual = await page.request.post('/api/v1/shelves', { headers, data: { name: manualNames.old } });
    expect(oldManual.ok(), await oldManual.text()).toBeTruthy();
    const oldManualId = (await oldManual.json() as { id: number }).id;
    createdManualIds.push(oldManualId);
    const newManual = await page.request.post('/api/v1/shelves', { headers, data: { name: manualNames.next } });
    expect(newManual.ok(), await newManual.text()).toBeTruthy();
    const newManualId = (await newManual.json() as { id: number }).id;
    createdManualIds.push(newManualId);
    for (const book of olderBooks) {
      const response = await page.request.post(`/api/v1/shelves/${oldManualId}/books/${book.id}`, { headers });
      expect(response.ok(), await response.text()).toBeTruthy();
    }
    const manualAdd = await page.request.post(`/api/v1/shelves/${newManualId}/books/${newBook.id}`, { headers });
    expect(manualAdd.ok(), await manualAdd.text()).toBeTruthy();
    endpoints.push({ kind: 'shelf', id: oldManualId, name: manualNames.old, firstBook: olderBooks[0], previous: olderBooks });
    endpoints.push({ kind: 'shelf', id: newManualId, name: manualNames.next, firstBook: newBook, previous: olderBooks });

    const createMagic = async (name: string, members: Book[]) => {
      const response = await page.request.post('/magicshelf', {
        headers,
        data: { name, icon: '📚', rules: { condition: 'OR', rules: members.map(book => ({
          id: 'title', operator: 'equal', value: book.title,
        })) } },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      const body = await response.json() as { success: boolean; shelf_id: number };
      expect(body.success).toBeTruthy();
      return body.shelf_id;
    };
    const oldMagicName = `2268 later smart ${secondaryUser.username}`;
    const nextMagicName = `2268 first smart ${secondaryUser.username}`;
    const oldMagicId = await createMagic(oldMagicName, olderBooks);
    createdMagicIds.push(oldMagicId);
    const newMagicId = await createMagic(nextMagicName, [newBook]);
    createdMagicIds.push(newMagicId);
    endpoints.push({ kind: 'magic', id: oldMagicId, name: oldMagicName, firstBook: olderBooks[0], previous: olderBooks });
    endpoints.push({ kind: 'magic', id: newMagicId, name: nextMagicName, firstBook: newBook, previous: olderBooks });

    for (const entry of endpoints) {
      const resource = entry.kind === 'shelf' ? 'shelves' : 'magicshelf';
      await page.route(`**/api/v1/${resource}/${entry.id}?*`, async route => {
        const url = new URL(route.request().url());
        if (!url.searchParams.has('select_all')) {
          url.searchParams.set('per_page', '1');
        }
        await route.continue({ url: url.toString() });
      });
    }

    for (const kind of ['shelf', 'magic'] as const) {
      const old = endpoints.find(entry => entry.kind === kind && entry.previous.length === 3)!;
      const next = endpoints.find(entry => entry.kind === kind && entry.previous.length === 3
        && entry.firstBook.id === newBook.id)!;
      const resource = kind === 'shelf' ? 'shelves' : 'magicshelf';
      const pageTwo = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === `/api/v1/${resource}/${old.id}`
          && url.searchParams.get('page') === '2';
      });
      await page.goto(`/app/${kind === 'shelf' ? 'shelf' : 'magic'}/${old.id}`);
      // Smart shelf headings include the shelf icon, so match the stable name
      // within the heading rather than requiring the icon-free string exactly.
      await expect(page.getByRole('heading', { name: old.name, exact: false })).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      expect((await pageTwo).ok()).toBeTruthy();

      const pageOneOfNext = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === `/api/v1/${resource}/${next.id}`
          && url.searchParams.get('page') === '1';
      });
      if ((await page.evaluate(() => window.innerWidth)) < 700) {
        await page.getByRole('button', { name: 'Open navigation' }).click();
      }
      await page.locator(`nav a[href="/app/${kind === 'shelf' ? 'shelf' : 'magic'}/${next.id}"]`).first().click();
      expect((await pageOneOfNext).ok()).toBeTruthy();
      await expect(page.getByRole('heading', { name: next.name, exact: false })).toBeVisible();
      await expect(page.getByRole('link', { name: `Open details for ${newBook.title}`, exact: true })).toBeVisible();
      await expect(page.locator(`main a[href="/book/${old.previous[0].id}"]`)).toHaveCount(0);
      await page.unroute(`**/api/v1/${resource}/${old.id}?*`);
      await page.unroute(`**/api/v1/${resource}/${next.id}?*`);
    }
  } finally {
    await Promise.all([
      ...createdManualIds.map(id => page.request.post(`/api/v1/shelves/${id}/delete`, { headers }).catch(() => {})),
      ...createdMagicIds.map(id => page.request.post(`/magicshelf/${id}/delete`, { headers }).catch(() => {})),
    ]);
  }
});

test('late selection cannot overwrite Clear or Done, and failed selection keeps prior IDs', async ({ secondaryUser }) => {
  const page = secondaryUser.page;
  const headers = await csrf(page);
  const shelfResponse = await page.request.post('/api/v1/shelves', {
    headers, data: { name: `2268 race ${secondaryUser.username}` },
  });
  expect(shelfResponse.ok(), await shelfResponse.text()).toBeTruthy();
  const shelfId = (await shelfResponse.json() as { id: number }).id;
  const booksResponse = await page.request.get('/api/v1/books?per_page=20&sort=new');
  const books = ((await booksResponse.json()) as { items: Book[] }).items;
  expect(books.length).toBeGreaterThan(2);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/app');
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('button', { name: `Select ${books[0].title}`, exact: true }).click();
  await page.getByRole('button', { name: 'Add to shelf', exact: true }).click();
  const shelfOption = page.getByRole('button', { name: `2268 race ${secondaryUser.username}`, exact: true });
  await expect(shelfOption).toBeVisible();

  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let routeFinished!: () => void;
  const requestFinished = new Promise<void>(resolve => { routeFinished = resolve; });
  let addRequests = 0;
  await page.route(`**/api/v1/shelves/${shelfId}/books/*`, async route => {
    addRequests += 1;
    await route.continue();
  });
  await page.route('**/api/v1/books?**select_all=1**', async route => {
    await held;
    await route.continue();
    routeFinished();
  });
  const pendingSelectAll = page.waitForRequest(request => request.url().includes('select_all=1'));
  const selectAllResponse = page.waitForResponse(response => response.url().includes('select_all=1'));
  await page.getByRole('button', { name: /^Select all \d+ books$/ }).click();
  await pendingSelectAll;
  await expect(shelfOption).toBeHidden();
  expect(addRequests).toBe(0);
  await expect(page.getByRole('button', { name: `Select ${books[1].title}`, exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Clear selection', exact: true }).click();
  release();
  const completedResponse = await selectAllResponse;
  expect(completedResponse.ok()).toBeTruthy();
  await requestFinished;
  await expect(page.getByRole('region', { name: /selected/ })).toHaveCount(0);

  // Done is another explicit cancellation. Let the real response finish before
  // asserting that its late completion did not restore the old selection.
  await page.unroute('**/api/v1/books?**select_all=1**');
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('button', { name: `Select ${books[0].title}`, exact: true }).click();
  let releaseDone!: () => void;
  const doneGate = new Promise<void>(resolve => { releaseDone = resolve; });
  let doneRouteFinished!: () => void;
  const doneRequestFinished = new Promise<void>(resolve => { doneRouteFinished = resolve; });
  await page.route('**/api/v1/books?**select_all=1**', async route => {
    await doneGate;
    await route.continue();
    doneRouteFinished();
  });
  const doneRequest = page.waitForRequest(request => request.url().includes('select_all=1'));
  const doneResponse = page.waitForResponse(response => response.url().includes('select_all=1'));
  await page.getByRole('button', { name: /^Select all \d+ books$/ }).click();
  await doneRequest;
  await page.getByRole('button', { name: 'Done', exact: true }).click();
  releaseDone();
  expect((await doneResponse).ok()).toBeTruthy();
  await doneRequestFinished;
  await expect(page.getByRole('region', { name: /selected/ })).toHaveCount(0);

  // 413 is a bounded failure response from the selection endpoint. Existing
  // single-book selection must remain retryable and the limit is visible.
  await page.unroute('**/api/v1/books?**select_all=1**');
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('button', { name: `Select ${books[0].title}`, exact: true }).click();
  await page.route('**/api/v1/books?**select_all=1**', async route => {
    await route.fulfill({ status: 413, contentType: 'application/json', body: JSON.stringify({ error: {
      code: 'selection_too_large', message: 'limited', max_items: 100000,
    } }) });
  });
  await page.getByRole('button', { name: /^Select all \d+ books$/ }).click();
  await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
  await expect(page.getByText(/Select all is limited to 100,?000 books/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('select-all-limit-error-desktop.png') });
});
