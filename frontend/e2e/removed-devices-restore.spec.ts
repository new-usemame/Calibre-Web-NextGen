import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

// #2765 follow-up: a removed KOReader or Kobo stops syncing, so the account
// needs a way to bring it back after the Undo notice is gone.
const active = {
  public_id: 'device-1', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour',
  firmware: '4.45.23684', first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-09T12:00:00',
  annotation_count: 3, active: true,
};
const removedFixture = {
  public_id: 'device-2', label: 'Clara BW', type: 'koreader', model: 'Kobo Clara BW',
  firmware: null, first_seen: '2026-07-01T12:00:00', last_seen: '2026-10-29T21:53:00',
  annotation_count: 0, active: false,
};

async function stubAccount(page: import('@playwright/test').Page) {
  let removed = { ...removedFixture };
  let restores = 0;
  await page.route('**/api/annotations/devices?*', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const url = new URL(route.request().url());
    const all = [active, removed];
    // The server's contract: active=true lists active devices only,
    // active=false lists removed devices only, no filter lists both.
    const filter = url.searchParams.get('active');
    const devices = filter === 'true' ? all.filter(d => d.active)
      : filter === 'false' ? all.filter(d => !d.active) : all;
    await route.fulfill({ json: { devices, limit: 100, offset: 0, total: devices.length } });
  });
  await page.route('**/api/annotations/devices/device-2/restore', async (route) => {
    restores += 1;
    removed = { ...removed, active: true };
    await route.fulfill({ json: { device: removed, restored_assignment_count: 0, assignment_conflict_count: 0 } });
  });
  return { restores: () => restores };
}

test('a removed device is listed apart and can be restored from the account page', async ({ page }) => {
  const calls = await stubAccount(page);
  await page.goto('/app/account/devices');

  const removedRegion = page.getByRole('region', { name: 'Removed devices' });
  await expect(removedRegion).toBeVisible({ timeout: 15000 });
  await expect(removedRegion.getByText('Clara BW', { exact: true })).toBeVisible();
  // The active device stays in the main list, not among the removed ones.
  await expect(removedRegion.getByText('Libra Colour', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Libra Colour', exact: true })).toBeVisible();

  await removedRegion.getByRole('button', { name: 'Restore Clara BW' }).click();
  expect(calls.restores()).toBe(1);
  await expect(page.getByRole('link', { name: 'Clara BW', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Removed devices' })).toHaveCount(0);

  await assertNoHorizontalOverflow(page);
  const scan = await new AxeBuilder({ page }).include('main').analyze();
  expect(scan.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
});

test('an account with no removed devices shows no removed section', async ({ page }) => {
  await page.route('**/api/annotations/devices?*', route => {
    const url = new URL(route.request().url());
    const devices = url.searchParams.get('active') === 'false' ? [] : [active];
    return route.fulfill({ json: { devices, limit: 100, offset: 0, total: devices.length } });
  });
  await page.goto('/app/account/devices');
  await expect(page.getByRole('link', { name: 'Libra Colour', exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('region', { name: 'Removed devices' })).toHaveCount(0);
});
