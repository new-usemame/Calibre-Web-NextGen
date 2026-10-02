import AxeBuilder from '@axe-core/playwright';
import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

const endpoint = '/api/v1/account/discover-source';
async function headers(page: Page) {
  const res = await page.request.get('/api/v1/auth/csrf');
  expect(res.ok()).toBeTruthy();
  return { 'X-CSRFToken': (await res.json() as { csrf_token: string }).csrf_token };
}
async function discoverIds(page: Page) {
  const res = await page.request.get('/api/v1/books?filter=discover&per_page=100');
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json() as { items: { id: number }[] }).items.map(book => book.id);
}

async function discoverSelection(page: Page) {
  const response = await page.request.get('/api/v1/books?filter=discover&select_all=1');
  expect(response.ok(), await response.text()).toBeTruthy();
  return await response.json() as { ids: number[]; total: number };
}

// The source belongs to an owned account, so changing it cannot alter parallel
// cases' shared seed login. Shelves and smart shelves are owned and cleaned too.
test('Discover uses the account shelf across New UI, Classic and OPDS and recovers from empty/deleted sources', async ({ page: admin, secondaryUser }, testInfo) => {
  test.setTimeout(120_000);
  const { page } = secondaryUser;
  const h = await headers(page); const ah = await headers(admin);
  const visibility = await page.request.post('/api/v1/account/sidebar', { headers: h, data: { visibility: { random: true } } });
  expect(visibility.ok(), await visibility.text()).toBeTruthy();
  const candidates = await discoverIds(page);
  expect(candidates.length).toBeGreaterThan(1);
  const chosen = candidates[0];
  const detail = await page.request.get(`/api/v1/books/${chosen}`);
  expect(detail.ok()).toBeTruthy();
  const book = await detail.json() as { title: string };
  const ownedShelves: number[] = [];
  let smartId: number | undefined;
  async function makeShelf(owner: Page, isPublic = false) {
    const response = await owner.request.post('/api/v1/shelves', {
      headers: owner === page ? h : ah,
      data: { name: `discover-${testInfo.project.name}-${Date.now()}-${ownedShelves.length}`, is_public: isPublic },
    });
    expect(response.status(), await response.text()).toBe(201);
    const id = (await response.json() as { id: number }).id;
    if (owner === page) ownedShelves.push(id);
    return id;
  }
  let publicShelf: number | undefined;
  const adminOriginal = await admin.request.get(endpoint);
  const adminSource = await adminOriginal.json();
  try {
    const shelf = await makeShelf(page);
    const added = await page.request.post(`/api/v1/shelves/${shelf}/books/${chosen}`, { headers: h });
    expect(added.ok(), await added.text()).toBeTruthy();
    const empty = await makeShelf(page);
    const source = `shelf:${shelf}`;
    const smart = await page.request.post('/magicshelf', { headers: h,
      data: { name: `discover-smart-${Date.now()}`, icon: '📚', rules: { condition: 'AND', rules: [{ id: 'title', operator: 'equal', value: book.title }] } },
    });
    expect(smart.ok(), await smart.text()).toBeTruthy();
    smartId = (await smart.json() as { shelf_id: number }).shelf_id;

    for (const width of [1280, 375]) {
      expect((await page.request.post('/api/v1/account/profile', { headers: h, data: { theme: width === 1280 ? 'light' : 'dark' } })).ok()).toBeTruthy();
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/app'); await page.reload();
      const strip = page.getByTestId('discover-section');
      const select = strip.getByLabel('Discover source', { exact: true });
      await select.selectOption(source);
      await page.route(`**${endpoint}`, route => route.request().method() === 'PUT'
        ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Test outage' } }) }) : route.continue());
      await strip.getByRole('button', { name: 'Save Discover source', exact: true }).click();
      await expect(strip.getByText('Could not save Discover source. Please try again.', { exact: true })).toBeVisible();
      await expect(select).toHaveValue(source);
      await select.focus(); await expect(select).toBeFocused();
      const axe = await new AxeBuilder({ page }).include('[data-testid="discover-section"]').analyze();
      expect(axe.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`new-error-${width}.jpg`), type: 'jpeg', quality: 75 });
      await page.unroute(`**${endpoint}`);
      await strip.getByRole('button', { name: 'Save Discover source', exact: true }).click();
      await expect(strip.getByText('Discover source saved.', { exact: true })).toBeVisible();
      await expect.poll(() => discoverIds(page)).toEqual([chosen]);
      await expect.poll(() => discoverSelection(page)).toEqual({ ids: [chosen], total: 1 });
      await expect(strip.locator('a[href*="/book/"]').filter({ hasText: book.title }).first()).toBeVisible();
      await strip.getByRole('button', { name: 'Shuffle picks' }).click();
      await expect(strip.getByRole('button', { name: 'Shuffle picks' })).toBeEnabled();
      await page.reload(); await expect(select).toHaveValue(source);
      await page.screenshot({ path: testInfo.outputPath(`new-shelf-${width}.jpg`), type: 'jpeg', quality: 75 });
      await page.goto('/app/discover');
      await expect(page.getByLabel('Discover source', { exact: true })).toHaveValue(source);
      await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]').filter({ hasText: book.title }).first()).toBeVisible();
      await page.getByLabel('Discover source', { exact: true }).selectOption(`shelf:${empty}`);
      await page.getByRole('button', { name: 'Save Discover source', exact: true }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Discover source saved.' })).toBeVisible();
      await expect.poll(() => discoverIds(page)).toEqual([]);
      await expect.poll(() => discoverSelection(page)).toEqual({ ids: [], total: 0 });
      await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]')).toHaveCount(0);
      await expect(page.getByLabel('Discover source', { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath(`new-empty-${width}.jpg`), type: 'jpeg', quality: 75 });
      await page.goto('/discover/stored');
      const classic = page.locator('#discover-source-form');
      await expect(classic.getByLabel('Discover source', { exact: true })).toHaveValue(`shelf:${empty}`);
      await classic.getByLabel('Discover source', { exact: true }).selectOption(source);
      await classic.getByRole('button', { name: 'Save Discover source', exact: true }).click();
      await expect(classic.getByLabel('Discover source', { exact: true })).toHaveValue(source);
      // Classic deliberately shortens visible titles; its title attribute keeps the exact identity.
      const classicTitle = page.locator(`.book a[href="/book/${chosen}"] .title`);
      await expect(classicTitle).toHaveCount(1);
      await expect(classicTitle).toHaveAttribute('title', book.title);
      await expect(classicTitle).toBeVisible();
      const classicAxe = await new AxeBuilder({ page }).include('#discover-source-form').analyze();
      expect(classicAxe.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`classic-shelf-${width}.jpg`), type: 'jpeg', quality: 75 });
    }
    const smartSave = await page.request.put(endpoint, { headers: h, data: { source: `smart:${smartId}` } });
    expect(smartSave.ok(), await smartSave.text()).toBeTruthy();
    const smartIds = await discoverIds(page);
    expect(smartIds).toContain(chosen);
    // All matches have the chosen title; seed catalogs may contain another edition.
    for (const id of smartIds) {
      const response = await page.request.get(`/api/v1/books/${id}`);
      expect((await response.json() as { title: string }).title).toBe(book.title);
    }
    publicShelf = await makeShelf(admin, true);
    expect((await admin.request.post(`/api/v1/shelves/${publicShelf}/books/${chosen}`, { headers: ah })).ok()).toBeTruthy();
    expect((await page.request.put(endpoint, { headers: h, data: { source: `shelf:${publicShelf}` } })).ok()).toBeTruthy();
    expect(await discoverIds(page)).toEqual([chosen]);
    expect((await admin.request.post(`/api/v1/shelves/${publicShelf}`, { headers: ah, data: { is_public: false } })).ok()).toBeTruthy();
    expect(await discoverIds(page)).toEqual([]);
    expect(await discoverSelection(page)).toEqual({ ids: [], total: 0 });
    await page.goto('/app');
    await expect(page.getByTestId('discover-section').getByText('This Discover source is unavailable. Choose another source.')).toBeVisible();
    await expect(page.getByLabel('Discover source', { exact: true })).toHaveValue(`shelf:${publicShelf}`);
    expect((await page.request.put(endpoint, { headers: h, data: { source: `shelf:${publicShelf}` } })).status()).toBe(400);
    expect((await page.request.put(endpoint, { headers: h, data: { source: source } })).ok()).toBeTruthy();
    const opds = await page.request.get('/opds/discover', { headers: { Authorization: `Basic ${Buffer.from(`${secondaryUser.username}:${secondaryUser.password}`).toString('base64')}` } });
    expect(opds.ok(), await opds.text()).toBeTruthy();
    const xml = await opds.text();
    const opdsEntries = await page.evaluate((payload) => {
      const document = new DOMParser().parseFromString(payload, 'application/xml');
      if (document.querySelector('parsererror')) throw new Error('OPDS response is not valid XML');
      return [...document.getElementsByTagNameNS('http://www.w3.org/2005/Atom', 'entry')]
        .map(entry => entry.getElementsByTagNameNS('http://www.w3.org/2005/Atom', 'title')[0]?.textContent);
    }, xml);
    expect(opdsEntries).toEqual([book.title]);
    expect((await page.request.post(`/api/v1/shelves/${shelf}/delete`, { headers: h })).ok()).toBeTruthy();
    ownedShelves.splice(ownedShelves.indexOf(shelf), 1);
    expect(await discoverIds(page)).toEqual([]);
    await page.goto('/app');
    await expect(page.getByLabel('Discover source', { exact: true })).toHaveValue(source);
    await expect(page.getByText('This Discover source is unavailable. Choose another source.')).toBeVisible();
    await page.getByLabel('Discover source', { exact: true }).selectOption('library');
    await page.getByRole('button', { name: 'Save Discover source', exact: true }).click();
    await expect.poll(async () => (await discoverIds(page)).length).toBeGreaterThan(1);
    expect(await (await admin.request.get(endpoint)).json()).toMatchObject({ source: adminSource.source, available: adminSource.available });
    expect((await page.request.put(endpoint, { data: { source: 'library' } })).status()).toBe(400);
    expect((await page.request.put(endpoint, { headers: { ...h, Origin: 'https://untrusted.invalid' }, data: { source: 'library' } })).status()).toBe(403);
  } finally {
    await page.unroute(`**${endpoint}`);
    if (smartId) await page.request.post(`/magicshelf/${smartId}/delete`, { headers: h });
    for (const id of ownedShelves) await page.request.post(`/api/v1/shelves/${id}/delete`, { headers: h });
    if (publicShelf) await admin.request.post(`/api/v1/shelves/${publicShelf}/delete`, { headers: ah });
  }
});

test('cold Discover waits for the saved source before showing an empty state', async ({ secondaryUser }) => {
  test.setTimeout(120_000);
  const { page } = secondaryUser;
  let nextGate: ReturnType<typeof gateNextSourceRead> | undefined;
  await page.route(`**${endpoint}`, async route => {
    const gate = nextGate;
    if (route.request().method() === 'GET' && gate) {
      nextGate = undefined;
      gate.signalStarted();
      await gate.wait;
    }
    await route.continue();
  });

  const landingGate = gateNextSourceRead();
  nextGate = landingGate;
  await page.goto('/app', { waitUntil: 'domcontentloaded' });
  await landingGate.started;
  const strip = page.getByTestId('discover-section');
  await expect(strip.getByRole('status').filter({ hasText: 'Loading…' })).toBeVisible();
  await expect(strip.getByText('No unread books in this Discover source.', { exact: true })).toHaveCount(0);
  landingGate.release();
  await expect.poll(async () => strip.locator('a[href*="/book/"]').count()).toBeGreaterThan(0);

  // A full document load starts a fresh query client and exercises the saved
  // Discover catalog's disabled-until-source-known path independently.
  const catalogGate = gateNextSourceRead();
  nextGate = catalogGate;
  await page.goto('/app/discover', { waitUntil: 'domcontentloaded' });
  await catalogGate.started;
  await expect(page.getByRole('status').filter({ hasText: 'Loading…' })).toBeVisible();
  await expect(page.getByText('No unread books in this Discover source.', { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]')).toHaveCount(0);
  catalogGate.release();
  await expect.poll(async () => page.getByTestId('catalog-grid').locator('a[href*="/book/"]').count())
    .toBeGreaterThan(0);
  await expect(page.getByText('No unread books in this Discover source.', { exact: true })).toHaveCount(0);

  // Changing source after the catalog has accumulated the default library must
  // discard those cards; returning from a book must restore only the new source.
  const before = await discoverIds(page);
  expect(before.length).toBeGreaterThan(1);
  const chosen = before[0];
  const detail = await page.request.get(`/api/v1/books/${chosen}`);
  expect(detail.ok()).toBeTruthy();
  const book = await detail.json() as { title: string };
  const csrf = await headers(page);
  const made = await page.request.post('/api/v1/shelves', {
    headers: csrf, data: { name: `discover-cache-${Date.now()}`, is_public: false },
  });
  expect(made.status(), await made.text()).toBe(201);
  const shelfId = (await made.json() as { id: number }).id;
  let primaryError: unknown;
  try {
    const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${chosen}`, { headers: csrf });
    expect(added.ok(), await added.text()).toBeTruthy();
    // Source options were fetched before this owned shelf was created.
    await page.reload();
    const select = page.getByLabel('Discover source', { exact: true });
    // A new source is a new action scope: books selected in the old sample
    // must not remain bulk-actionable when those cards disappear.
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    const all = page.getByRole('button', { name: /^Select all \d+ books$/ });
    await expect(all).toBeEnabled();
    await all.click();
    const selection = page.getByRole('region', { name: /^\d+ selected$/ });
    await expect(selection).toBeVisible();
    expect(Number.parseInt(await selection.getAttribute('aria-label') ?? '0', 10)).toBeGreaterThan(1);
    await select.selectOption(`shelf:${shelfId}`);
    await page.getByRole('button', { name: 'Save Discover source', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Discover source saved.' })).toBeVisible();
    await expect.poll(() => discoverIds(page)).toEqual([chosen]);
    const grid = page.getByTestId('catalog-grid');
    await expect(grid.locator('[data-book-id]')).toHaveCount(1);
    await expect(selection).toHaveCount(0);
    await expect(all).toHaveText('Select all 1 books');
    await expect(all).toBeEnabled();
    await all.click();
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
    expect(await discoverSelection(page)).toEqual({ ids: [chosen], total: 1 });
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    const gridLinks = grid.locator('a[href*="/book/"]');
    await expect(gridLinks).toHaveCount(1);
    await expect(gridLinks.first()).toHaveAttribute('aria-label', `Open details for ${book.title}`);
    await gridLinks.first().click();
    await page.waitForURL(url => url.pathname.endsWith(`/book/${chosen}`));
    await page.goBack();
    await page.waitForURL(url => url.pathname.endsWith('/app/discover'));
    await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]')).toHaveCount(1);
    await expect(page.getByTestId('catalog-grid').getByRole('link', {
      name: `Open details for ${book.title}`, exact: true,
    })).toBeVisible();
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    try {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers: csrf });
    } catch (cleanupError) {
      if (primaryError === undefined) throw cleanupError;
      console.warn('Could not remove the owned Discover test shelf after the primary test failure:', cleanupError);
    }
    await page.unroute(`**${endpoint}`);
  }
});

function gateNextSourceRead() {
  let signalStarted!: () => void;
  let release!: () => void;
  return {
    started: new Promise<void>(resolve => { signalStarted = resolve; }),
    wait: new Promise<void>(resolve => { release = resolve; }),
    signalStarted: () => signalStarted(),
    release: () => release(),
  };
}
