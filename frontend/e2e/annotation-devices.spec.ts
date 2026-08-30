import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const device = {
  public_id: 'device-1', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour',
  firmware: '4.45.23684', first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-09T12:00:00',
  annotation_count: 312, active: true,
};

async function stubDevices(page: import('@playwright/test').Page) {
  let current = { ...device };
  let restored = 0;
  await page.route('**/api/annotations/devices?*', async (route) => {
    if (route.request().method() === 'GET') {
      const devices = current.active ? [current] : [];
      await route.fulfill({ json: { devices, limit: 100, offset: 0, total: devices.length } });
    } else await route.continue();
  });
  await page.route('**/api/annotations/devices/device-1/delete-preflight', (route) =>
    route.fulfill({ json: { origin_count: 4, assigned_count: 2 } }));
  await page.route('**/api/annotations/devices/device-1', async (route) => {
    if (route.request().method() === 'PATCH') {
      current = { ...current, label: (await route.request().postDataJSON()).label };
      await route.fulfill({ json: current });
    } else if (route.request().method() === 'DELETE') {
      current = { ...current, active: false };
      await route.fulfill({ json: { device: current, origin_count: 4, assigned_count: 2 } });
    } else await route.continue();
  });
  await page.route('**/api/annotations/devices/device-1/restore', async (route) => {
    restored += 1;
    current = { ...current, active: true };
    await route.fulfill({ json: { device: current, restored_assignment_count: 2, assignment_conflict_count: 0 } });
  });
  return { restored: () => restored };
}

test('device manager renames and removes only through counted confirmation, then restores', async ({ page }) => {
  const calls = await stubDevices(page);
  await page.goto('/app/account/devices');
  await expect(page.getByRole('heading', { name: 'E-readers' })).toBeVisible();
  await expect(page.getByText('312 highlights and notes')).toBeVisible();

  await page.getByRole('button', { name: 'Rename Libra Colour' }).click();
  const input = page.getByRole('textbox', { name: 'Device name' });
  await input.fill('Travel Kobo');
  await input.press('Enter');
  await expect(page.getByRole('heading', { name: 'Travel Kobo' })).toBeVisible();

  await page.getByRole('button', { name: 'More actions for Travel Kobo' }).click();
  await page.getByRole('button', { name: 'Remove device' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Remove Travel Kobo?' });
  await expect(dialog).toContainText('4 highlights and notes were made on this device');
  await expect(dialog).toContainText('2 highlights and notes assigned to this device');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Remove device' }).click();
  await expect(page.getByText('Travel Kobo removed.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('heading', { name: 'Travel Kobo' })).toBeVisible();
  expect(calls.restored()).toBe(1);
});

test('device manager is axe-clean and has no 390px overflow', async ({ page }, testInfo) => {
  await stubDevices(page);
  if (testInfo.project.name === 'desktop') await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app/account/devices');
  await expect(page.getByRole('heading', { name: 'E-readers' })).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
  await assertNoHorizontalOverflow(page);
});

test('device inventory renders one bounded window and reports the true total', async ({ page }) => {
  let inventoryRequestUrl: URL | null = null;
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: {
    devices: [{ ...device, inventory_count: 5000, inventory_observed: '2026-08-09T12:00:00' }],
    limit: 100, offset: 0, total: 1,
  } }));
  await page.route('**/api/annotations/devices/device-1/inventory?*', (route) => {
    inventoryRequestUrl = new URL(route.request().url());
    return route.fulfill({ json: {
      observed_at: '2026-08-09T12:00:00',
      limit: 200,
      offset: 0,
      total: 5000,
      books: Array.from({ length: 5000 }, (_, index) => ({
        book_id: index + 1,
        lpath: `Books/${String(index).padStart(4, '0')}.epub`,
        checksum: index.toString(16).padStart(32, '0'),
        size: index,
        mtime: index,
      })),
    } });
  });

  await page.goto('/app/account/devices');
  await page.getByRole('button', { name: 'View device library' }).click();
  const inventory = page.locator('#device-inventory-device-1');
  await expect(inventory.getByRole('status')).toHaveText(
    'Showing 200 of 5000 books from the latest device inventory.',
  );
  await expect(inventory.getByRole('listitem')).toHaveCount(200);
  await expect(inventory.getByRole('link')).toHaveCount(200);
  const deleteGeometry = await inventory.getByRole('button', { name: 'Delete from device' })
    .evaluateAll((buttons) => {
      const first = buttons[0].getBoundingClientRect();
      const second = buttons[1].getBoundingClientRect();
      return { height: first.height, neighborGap: second.top - first.bottom };
    });
  expect(deleteGeometry.height).toBeGreaterThanOrEqual(44);
  expect(deleteGeometry.neighborGap).toBeGreaterThanOrEqual(24);
  expect(inventoryRequestUrl?.searchParams.get('limit')).toBe('200');
  expect(inventoryRequestUrl?.searchParams.get('offset')).toBe('0');
  await inventory.getByRole('button', { name: 'Next' }).click();
  await expect.poll(() => inventoryRequestUrl?.searchParams.get('offset')).toBe('200');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((violation) => (
    ['critical', 'serious'].includes(violation.impact || '')
  ))).toEqual([]);
});

test('account summary makes the e-reader manager discoverable', async ({ page }) => {
  await stubDevices(page);
  await page.goto('/app/account');
  const card = page.getByRole('region', { name: 'E-readers' });
  await expect(card).toContainText('Libra Colour · 312 highlights and notes');
  await expect(card.getByRole('link', { name: 'Manage e-readers' })).toHaveAttribute('href', '/app/account/devices');
});

// The plugin that produces this page's inventory and storage figures had no
// route from the SPA: its setup page was linked only from the classic admin
// screen, so a KOReader user on the default surface could not reach the
// download or the install steps. Pin both setup routes, not just the new one —
// a later tidy-up of this section must not silently drop either.
test('device manager routes to both e-reader setup pages', async ({ page }) => {
  await stubDevices(page);
  await page.goto('/app/account/devices');
  await expect(page.getByRole('link', { name: 'Setup KOReader Sync' }))
    .toHaveAttribute('href', '/kosync');
  await expect(page.getByRole('link', { name: 'Set up Kobo sync' }))
    .toHaveAttribute('href', '/me');
});
