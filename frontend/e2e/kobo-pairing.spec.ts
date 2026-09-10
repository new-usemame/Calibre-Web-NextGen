import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const syncUrl = 'https://books.example.test/kobo/0123456789abcdef0123456789abcdef';
const serverUrl = 'https://books.example.test';

async function enableKoboFeature(page: Page) {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    await route.fulfill({ response, json: {
      ...me,
      features: { ...(me.features ?? {}), kobo_sync: true },
    } });
  });
}

test('user generates settings, copies them, and confirms the first device check-in', async ({ page, context }) => {
  await enableKoboFeature(page);
  let generated = false;
  let checked = false;

  await page.route('**/api/v1/account/kobo-sync-token', async (route) => {
    if (route.request().method() === 'POST') generated = true;
    await route.fulfill({
      status: generated ? (route.request().method() === 'POST' ? 201 : 200) : 200,
      json: {
        user_id: 1,
        configured: generated,
        sync_url: generated ? syncUrl : null,
        server_url: serverUrl,
        is_localhost: false,
      },
    });
  });
  await page.route('**/api/annotations/devices?*', async (route) => {
    const devices = checked ? [{
      public_id: 'paired-kobo', label: 'Kitchen Kobo', type: 'kobo', model: 'Kobo Clara',
      firmware: '4.41', first_seen: '2026-08-30T18:00:00Z', last_seen: '2026-08-30T18:01:00Z',
      annotation_count: 0, inventory_count: 0, inventory_observed: null,
      storage_free: null, storage_total: null, storage_observed: null, active: true,
    }] : [];
    await route.fulfill({ json: { devices, limit: 100, offset: 0, total: devices.length } });
  });

  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/app/account/devices#kobo-pairing');
  const pairing = page.getByRole('region', { name: 'Pair a Kobo or KOReader' });
  await expect(pairing.getByRole('button', { name: 'Generate sync URL' })).toBeVisible();
  await pairing.getByRole('button', { name: 'Generate sync URL' }).click();

  await expect(pairing.getByText(syncUrl, { exact: true })).toBeVisible();
  await expect(pairing.getByText(`api_endpoint=${syncUrl}`, { exact: true })).toBeVisible();
  await expect(pairing.getByText(serverUrl, { exact: true })).toBeVisible();
  // A copy button must not squeeze the settings into a few characters per
  // line inside the desktop pairing columns. This also protects translations.
  for (const address of [syncUrl, serverUrl]) {
    const box = await pairing.getByText(address, { exact: true }).boundingBox();
    expect(box?.width, `readable address width for ${address}`).toBeGreaterThanOrEqual(160);
  }
  await expect(pairing).toContainText('.kobo/Kobo/Kobo eReader.conf');
  await expect(pairing).toContainText('the plugin adds /kosync itself');
  await expect(pairing.getByRole('link', { name: 'Install or update the NextGen Sync plugin.' }))
    .toHaveAttribute('href', '/kosync');

  await pairing.getByRole('button', { name: 'Copy sync URL' }).click();
  await expect(pairing.getByRole('button', { name: 'Copied' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(syncUrl);

  checked = true;
  await pairing.getByRole('button', { name: 'Check again' }).click();
  await expect(pairing.getByRole('status')).toContainText('Device seen: Kitchen Kobo');
  await expect(pairing.getByRole('status')).toContainText('Pairing is working.');

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((violation) => (
    ['critical', 'serious'].includes(violation.impact || '')
  ))).toEqual([]);
  await assertNoHorizontalOverflow(page);
});

test('account distinguishes loading sources from an empty account', async ({ page }) => {
  let releaseDevices!: () => void;
  const devicesReady = new Promise<void>((resolve) => { releaseDevices = resolve; });
  await page.route('**/api/annotations/devices?*', async (route) => {
    await devicesReady;
    await route.fulfill({ json: { devices: [], limit: 100, offset: 0, total: 0 } });
  });
  try {
    await page.goto('/app/account');
    const sources = page.getByRole('region', { name: 'Devices and browsers' });
    await expect(sources.getByRole('status')).toBeVisible();
    await expect(sources.getByText('No devices or browser reading data yet.')).toHaveCount(0);
    releaseDevices();
    await expect(sources.getByText('No devices or browser reading data yet.')).toBeVisible();
  } finally {
    releaseDevices();
  }
});

test('account links directly to the SPA pairing section', async ({ page }) => {
  await enableKoboFeature(page);
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: {
    devices: [], limit: 100, offset: 0, total: 0,
  } }));
  await page.goto('/app/account');
  const pair = page.getByRole('link', { name: 'Pair a Kobo or KOReader' });
  await expect(pair).toHaveAttribute('href', '/app/account/devices#kobo-pairing');
  await pair.click();
  const pairing = page.getByRole('region', { name: 'Pair a Kobo or KOReader' });
  await expect(pairing.getByRole('heading', { name: 'Pair a Kobo or KOReader' })).toBeInViewport();
  await expect(pairing.getByRole('heading', { name: 'Stock Kobo', exact: true })).toBeVisible();
  await expect(pairing.getByRole('heading', { name: 'KOReader', exact: true })).toBeVisible();
});

test('KOReader setup remains discoverable without stock Kobo sync or a token', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    await route.fulfill({ response, json: {
      ...me,
      features: { ...(me.features ?? {}), kobo_sync: false },
    } });
  });
  await page.route('**/api/v1/account/kobo-sync-token', (route) => route.fulfill({ json: {
    user_id: 1,
    configured: false,
    sync_url: null,
    server_url: serverUrl,
    is_localhost: false,
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: {
    devices: [], limit: 100, offset: 0, total: 0,
  } }));

  await page.goto('/app/account/devices#kobo-pairing');
  const pairing = page.getByRole('region', { name: 'Pair a Kobo or KOReader' });
  await expect(pairing.getByRole('heading', {
    level: 3, name: 'KOReader', exact: true,
  })).toBeVisible();
  await expect(pairing.getByRole('link', { name: 'Install or update the NextGen Sync plugin.' }))
    .toHaveAttribute('href', '/kosync');
  await expect(pairing.getByText(serverUrl, { exact: true })).toBeVisible();
  await expect(pairing.getByRole('button', { name: 'Copy server address' })).toBeVisible();
  await expect(pairing.getByRole('button', { name: 'Generate sync URL' })).toHaveCount(0);
  await expect(pairing.getByRole('status').filter({
    hasText: /^Kobo sync is not enabled on this server\.$/,
  })).toHaveText('Kobo sync is not enabled on this server.');
});
