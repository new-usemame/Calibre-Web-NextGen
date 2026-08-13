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
      await route.fulfill({ json: { devices: current.active ? [current] : [] } });
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

test('account summary makes the e-reader manager discoverable', async ({ page }) => {
  await stubDevices(page);
  await page.goto('/app/account');
  const card = page.getByRole('region', { name: 'E-readers' });
  await expect(card).toContainText('Libra Colour · 312 highlights and notes');
  await expect(card.getByRole('link', { name: 'Manage e-readers' })).toHaveAttribute('href', '/app/account/devices');
});
