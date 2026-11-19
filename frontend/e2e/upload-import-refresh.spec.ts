import { test, expect, type Page } from '@playwright/test';

// Exercise the real upload mutation, app-shell watcher and catalog cache.
// Only the upload/ingest hand-off is simulated: no shared library writes.
async function uploadScenario(page: Page, options: { missingBaseline?: boolean; failedProbes?: boolean } = {}) {
  const response = await page.request.get('/api/v1/books?page=1&per_page=24&sort=new');
  expect(response.ok()).toBeTruthy();
  const seed = await response.json();
  expect(seed.items.length).toBeGreaterThan(0);
  let queued = false;
  let arrivals = 0;
  let probes = 0;
  const title = 'Upload watcher arrival';
  let releaseProbe: (() => void) | undefined;
  const probeGate = new Promise<void>((resolve) => { releaseProbe = resolve; });

  await page.route('**/api/v1/books?**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('per_page') === '1') {
      probes += 1;
      if (queued && options.missingBaseline) await probeGate;
      if ((!queued && options.missingBaseline) || (queued && options.failedProbes)) {
        await route.fulfill({ status: 503, json: { error: { message: 'Temporary catalog failure' } } });
      } else {
        await route.fulfill({ json: { items: [], total: 100 + arrivals, page: 1, per_page: 1 } });
      }
      return;
    }
    const catalog = structuredClone(seed);
    if (arrivals > 0 && catalog.items?.length) catalog.items[0].title = title;
    await route.fulfill({ json: catalog });
  });
  await page.route('**/api/v1/upload', async (route) => {
    queued = true;
    await route.fulfill({ json: { queued: ['arrival.epub'], errors: [] } });
  });

  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  // Navigate within the SPA so the catalog is already cached before uploading.
  await page.getByRole('link', { name: /upload books/i }).first().click();
  await page.clock.install();
  await page.getByLabel('Choose books to upload').setInputFiles({
    name: 'arrival.epub', mimeType: 'application/epub+zip', buffer: Buffer.from('simulated ingest'),
  });
  await expect(page.getByText('1 file queued for import', { exact: true })).toBeVisible();
  await expect.poll(() => probes).toBeGreaterThan(1);
  return { title, arrive: () => { arrivals += 1; releaseProbe?.(); }, probes: () => probes };
}

test('an arrival refreshes the cached library after leaving Upload, then polling stops', async ({ page }) => {
  const scenario = await uploadScenario(page);
  await page.getByRole('link', { name: 'Back to library', exact: true }).click();
  scenario.arrive();
  await page.clock.runFor(3_100);
  await expect(page.getByText(scenario.title, { exact: true }).first()).toBeVisible();
  const completed = scenario.probes();
  await page.clock.runFor(12_000);
  expect(scenario.probes()).toBe(completed);
});

test('an unavailable pre-upload total still refreshes books already imported at the first probe', async ({ page }) => {
  const scenario = await uploadScenario(page, { missingBaseline: true });
  await page.getByRole('link', { name: 'Back to library', exact: true }).click();
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
  scenario.arrive();
  await expect(page.getByText(scenario.title, { exact: true }).first()).toBeVisible();
});

test('returning to a visible tab probes immediately even with global focus refetch disabled', async ({ page }) => {
  const scenario = await uploadScenario(page);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
  });
  const hidden = scenario.probes();
  await page.clock.runFor(10_000);
  expect(scenario.probes()).toBe(hidden);
  scenario.arrive();
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
  });
  await expect(page.getByText('1 of 1 imported', { exact: true })).toBeVisible({ timeout: 1_500 });
});

test('the watch reaches its stall deadline even when every probe fails', async ({ page }) => {
  const scenario = await uploadScenario(page, { failedProbes: true });
  await page.clock.fastForward(5 * 60_000 + 1);
  await expect(page.getByText(/No new books have appeared for a while/)).toBeVisible();
  // Let an already-running fetch settle, then verify no new interval starts.
  await page.waitForLoadState('networkidle');
  const stopped = scenario.probes();
  await page.clock.runFor(30_000);
  expect(scenario.probes()).toBe(stopped);
});


test('a second upload joins the first even when the first import finishes during its request', async ({ page }) => {
  const scenario = await uploadScenario(page);
  let releaseUpload: (() => void) | undefined;
  const uploadGate = new Promise<void>((resolve) => { releaseUpload = resolve; });
  let secondPosted = false;
  await page.route('**/api/v1/upload', async (route) => {
    secondPosted = true;
    await uploadGate;
    await route.fulfill({ json: { queued: ['second.epub'], errors: [] } });
  });
  await page.getByLabel('Choose books to upload').setInputFiles({
    name: 'second.epub', mimeType: 'application/epub+zip', buffer: Buffer.from('second simulated ingest'),
  });
  await expect.poll(() => secondPosted).toBe(true);
  const firstImported = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/me'));
  scenario.arrive();
  await page.clock.runFor(3_100);
  await firstImported;
  releaseUpload?.();
  await expect(page.getByText('1 of 2 imported', { exact: true })).toBeVisible();
  scenario.arrive();
  await page.clock.runFor(3_100);
  await expect(page.getByText('2 of 2 imported', { exact: true })).toBeVisible();
});
