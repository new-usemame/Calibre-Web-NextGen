import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const device = {
  public_id: 'device-1', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour',
  firmware: '4.45.23684', first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-09T12:00:00',
  annotation_count: 312, active: true,
};

test('device manager keeps its heading while loading and can retry a failed list', async ({ page }) => {
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  let recovered = false;
  await page.route('**/api/annotations/devices?*', async route => {
    await ready;
    if (!recovered) await route.fulfill({ status: 503, json: { error: 'unavailable' } });
    else await route.fulfill({ json: { devices: [device], total: 1, limit: 100, offset: 0 } });
  });
  try {
    await page.goto('/app/account/devices');
    await expect(page.getByRole('heading', { name: 'Devices and browsers', exact: true })).toBeVisible();
    await expect(page.getByRole('status', { name: 'Loading' })).toBeVisible();
    await expect(page.getByText('No devices or browser reading data yet.')).toHaveCount(0);
    release();
    await expect(page.getByRole('alert').filter({ hasText: 'Could not load devices and browsers.' })).toBeVisible({ timeout: 15000 });
    recovered = true;
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(page.getByRole('link', { name: device.label, exact: true })).toBeVisible();
    await expect(page.getByText('Could not load devices and browsers.', { exact: true })).toHaveCount(0);
  } finally { release(); }
});

test('reader sources separate browsers and distinguish missing from empty inventory', async ({ page }) => {
  const physical = { ...device, origin_annotation_count: 19, annotation_count: 0,
    inventory_count: 0, inventory_observed: null };
  const browser = { ...physical, public_id: 'browser-1', type: 'webreader', kind: 'webreader',
    label: 'Browser', model: 'CWNG web reader', browser_identity: 'account',
    origin_annotation_count: 2 };
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices: [physical, browser], total: 2, limit: 100, offset: 0,
  } }));
  let observed = false;
  await page.route('**/api/annotations/devices/device-1/inventory?*', route => route.fulfill({ json: {
    books: [], total: 0, limit: 200, offset: 0,
    observed_at: observed ? '2026-09-08T12:00:00Z' : null,
  } }));
  await page.goto('/app/account/devices');
  const browsers = page.getByRole('region', { name: 'Browser reading source' });
  await expect(browsers.getByRole('link', { name: 'Browser', exact: true })).toBeVisible();
  await expect(browsers.getByRole('listitem')).toHaveCount(1);
  await expect(browsers).toContainText('All browsers and computers signed in to your account share one Browser reading source.');
  await expect(browsers.getByRole('button', { name: 'View device library' })).toHaveCount(0);
  await expect(browsers.getByText(/books in latest inventory/)).toHaveCount(0);
  const clara = page.getByRole('listitem').filter({ has: page.getByRole('link', { name: device.label, exact: true }) });
  await expect(clara.getByText('19 annotations from this source', { exact: true })).toBeVisible();
  await expect(clara.getByText('Inventory not reported', { exact: true })).toBeVisible();
  await clara.getByRole('button', { name: 'View device library' }).click();
  await expect(clara.getByRole('status')).toHaveText('This device has not reported its inventory yet.');
  observed = true;
  await page.reload();
  await clara.getByRole('button', { name: 'View device library' }).click();
  await expect(clara.getByRole('status')).toHaveText('No books were reported in the latest device inventory.');
  await assertNoHorizontalOverflow(page);
  const scan = await new AxeBuilder({ page }).include('main').analyze();
  expect(scan.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
});

async function stubDevices(page: import('@playwright/test').Page, fixture = device) {
  let current = { ...fixture };
  let restored = 0;
  let deletePreflights = 0;
  await page.route('**/api/annotations/devices?*', async (route) => {
    if (route.request().method() === 'GET') {
      const devices = current.active ? [current] : [];
      await route.fulfill({ json: { devices, limit: 100, offset: 0, total: devices.length } });
    } else await route.continue();
  });
  await page.route('**/api/annotations/devices/device-1/delete-preflight', (route) => {
    deletePreflights += 1;
    return route.fulfill({ json: { origin_count: 4, assigned_count: 2 } });
  });
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
  return {
    restored: () => restored,
    deletePreflights: () => deletePreflights,
  };
}

test('device actions menu dismisses on an outside pointer press', async ({ page }) => {
  const calls = await stubDevices(page);
  await page.goto('/app/account/devices');

  const trigger = page.getByRole('button', { name: 'More actions for Libra Colour' });
  const remove = page.getByRole('button', { name: 'Remove device' });
  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(remove).toBeVisible();

  const heading = page.getByRole('heading', { name: 'Devices and browsers' });
  const box = await heading.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await expect(remove).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await page.mouse.up();

  await trigger.click();
  await expect(remove).toBeVisible();
  const topBar = page.getByRole('banner');
  const topBarBox = await topBar.boundingBox();
  expect(topBarBox).not.toBeNull();
  await page.mouse.move(
    topBarBox!.x + topBarBox!.width / 2,
    topBarBox!.y + topBarBox!.height / 2,
  );
  await page.mouse.down();
  await expect(remove).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await page.mouse.up();
  expect(calls.deletePreflights()).toBe(0);
});

test('Escape dismisses the device actions menu and restores trigger focus', async ({ page }) => {
  const calls = await stubDevices(page);
  await page.goto('/app/account/devices');

  const trigger = page.getByRole('button', { name: 'More actions for Libra Colour' });
  const remove = page.getByRole('button', { name: 'Remove device' });
  await trigger.click();
  await expect(remove).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(remove).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
  expect(calls.deletePreflights()).toBe(0);
});

test('touch dismissal does not activate the control beneath the press', async ({ page }) => {
  test.skip(test.info().project.use.hasTouch !== true, 'requires real touch input');

  const calls = await stubDevices(page);
  await page.goto('/app/account/devices');

  const trigger = page.getByRole('button', { name: 'More actions for Libra Colour' });
  const inventory = page.getByRole('button', { name: 'View device library' });
  const remove = page.getByRole('button', { name: 'Remove device' });
  await trigger.tap();
  await expect(remove).toBeVisible();

  const box = await inventory.boundingBox();
  expect(box).not.toBeNull();
  await page.touchscreen.tap(box!.x + box!.width / 2, box!.y + box!.height / 2);

  await expect(remove).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(inventory).toHaveAttribute('aria-expanded', 'false');

  await trigger.tap();
  await expect(remove).toBeVisible();
  const navigationToggle = page.getByRole('button', { name: 'Open navigation' });
  const navigation = page.locator('nav[aria-label="Browse"]');
  const navigationToggleBox = await navigationToggle.boundingBox();
  expect(navigationToggleBox).not.toBeNull();
  await page.touchscreen.tap(
    navigationToggleBox!.x + navigationToggleBox!.width / 2,
    navigationToggleBox!.y + navigationToggleBox!.height / 2,
  );

  await expect(remove).toBeHidden();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(navigation).toHaveAttribute('inert', '');
  expect(calls.deletePreflights()).toBe(0);
});

test('device manager renames and removes only through counted confirmation, then restores', async ({ page }) => {
  const calls = await stubDevices(page);
  await page.goto('/app/account/devices');
  await expect(page.getByRole('heading', { name: 'Devices and browsers' })).toBeVisible();
  await expect(page.getByText('312 annotations assigned to this source')).toBeVisible();

  await page.getByRole('button', { name: 'Rename Libra Colour' }).click();
  const input = page.getByRole('textbox', { name: 'Device name' });
  await input.fill('Travel Kobo');
  await input.press('Enter');
  await expect(page.getByRole('heading', { name: 'Travel Kobo' })).toBeVisible();

  await page.getByRole('button', { name: 'More actions for Travel Kobo' }).click();
  await page.getByRole('button', { name: 'Remove device' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Remove Travel Kobo?' });
  await expect(dialog).toContainText('4 annotations were made on this source');
  await expect(dialog).toContainText('2 annotations assigned to this source');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Remove device' }).click();
  await expect(page.getByText('Travel Kobo removed.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('heading', { name: 'Travel Kobo' })).toBeVisible();
  expect(calls.restored()).toBe(1);
});

test('failed device changes explain the failure and retain a working retry', async ({ page }) => {
  await stubDevices(page);
  // Fail each wire operation once; the original handlers provide the retry.
  for (const [path, method] of [
    ['device-1', 'PATCH'], ['device-1/delete-preflight', 'GET'],
    ['device-1', 'DELETE'], ['device-1/restore', 'POST'],
  ]) {
    let failed = false;
    await page.route(`**/api/annotations/devices/${path}`, async route => {
      if (!failed && route.request().method() === method) {
        failed = true;
        await route.fulfill({ status: 503, json: { error: 'Temporarily unavailable' } });
      } else await route.fallback();
    });
  }
  await page.goto('/app/account/devices');
  await page.getByRole('button', { name: 'Rename Libra Colour' }).click();
  const input = page.getByRole('textbox', { name: 'Device name' });
  await input.fill('Travel Kobo');
  await input.press('Enter');
  await expect(page.getByRole('alert').filter({ hasText: 'Could not rename' })).toBeVisible();
  await expect(input).toHaveValue('Travel Kobo');
  await input.press('Enter');
  await expect(page.getByRole('heading', { name: 'Travel Kobo' })).toBeVisible();

  await page.getByRole('button', { name: 'More actions for Travel Kobo' }).click();
  await page.getByRole('button', { name: 'Remove device', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load removal details' })).toBeVisible();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Remove device', exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await dialog.getByRole('button', { name: 'Remove device', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('Could not remove');
  await expect(page.getByRole('heading', { name: 'Travel Kobo', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Remove device', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Could not restore' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Travel Kobo', exact: true })).toBeVisible();
});

test('canceling a device removal returns keyboard focus to its surviving action', async ({ page }) => {
  await stubDevices(page);
  await page.goto('/app/account/devices');
  const trigger = page.getByRole('button', { name: 'More actions for Libra Colour' });
  await trigger.click();
  await page.getByRole('button', { name: 'Remove device', exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: 'Remove device', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('slow device changes cannot be restarted or leak an undo error to another source', async ({ page }) => {
  let rows = [{ ...device }, { ...device, public_id: 'device-2', label: 'Second Kobo' }];
  let finishRename!: () => void;
  let finishRemove!: () => void;
  const renameReady = new Promise<void>(resolve => { finishRename = resolve; });
  const removeReady = new Promise<void>(resolve => { finishRemove = resolve; });
  let patches = 0;
  let deletes = 0;
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices: rows, total: rows.length, limit: 100, offset: 0,
  } }));
  await page.route('**/api/annotations/devices/*/delete-preflight', route => route.fulfill({ json: { origin_count: 0, assigned_count: 0 } }));
  await page.route('**/api/annotations/devices/*/restore', route => route.fulfill({ status: 503, json: { error: 'unavailable' } }));
  await page.route('**/api/annotations/devices/device-*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').pop()!;
    if (route.request().method() === 'PATCH') {
      patches++;
      await renameReady;
      rows = rows.map(row => row.public_id === id ? { ...row, label: 'Travel Kobo' } : row);
      await route.fulfill({ json: rows.find(row => row.public_id === id) });
    } else if (route.request().method() === 'DELETE') {
      deletes++;
      if (id === 'device-1') await removeReady;
      rows = rows.filter(row => row.public_id !== id);
      await route.fulfill({ json: {} });
    } else await route.fallback();
  });
  try {
    await page.goto('/app/account/devices');
    const rename = page.getByRole('button', { name: 'Rename Libra Colour', exact: true });
    await rename.click();
    const input = page.getByRole('textbox', { name: 'Device name' });
    await input.fill('Travel Kobo');
    await input.press('Enter');
    await expect.poll(() => patches).toBe(1);
    await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled();
    await expect(rename).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(input).toBeVisible();
    finishRename();
    await expect(page.getByRole('heading', { name: 'Travel Kobo', exact: true })).toBeVisible();
    const travelRename = page.getByRole('button', { name: 'Rename Travel Kobo', exact: true });
    await expect(travelRename).toBeFocused();
    await travelRename.click();
    await input.press('Escape');
    await expect(travelRename).toBeFocused();

    await page.getByRole('button', { name: 'More actions for Travel Kobo' }).click();
    await page.getByRole('button', { name: 'Remove device', exact: true }).click();
    const dialog = page.getByRole('alertdialog');
    await dialog.getByRole('button', { name: 'Remove device', exact: true }).click();
    await expect.poll(() => deletes).toBe(1);
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'More actions for Second Kobo' })).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
    finishRemove();
    await expect(page.getByRole('button', { name: 'Undo' })).toBeFocused();
    await page.getByRole('button', { name: 'Undo' }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'Could not restore' })).toBeVisible();
    await page.getByRole('button', { name: 'More actions for Second Kobo' }).click();
    await page.getByRole('button', { name: 'Remove device', exact: true }).click();
    await dialog.getByRole('button', { name: 'Remove device', exact: true }).click();
    await expect(page.getByText('Second Kobo removed.', { exact: true })).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: 'Could not restore' })).toHaveCount(0);
    expect(patches).toBe(1);
    expect(deletes).toBe(2);
  } finally { finishRename(); finishRemove(); }
});

test('device manager keeps long source names readable without narrow-screen overflow', async ({ page }, testInfo) => {
  await stubDevices(page, { ...device, label: 'ReadingCompanion'.repeat(4) });
  if (testInfo.project.name === 'desktop') await page.setViewportSize({ width: 320, height: 844 });
  await page.goto('/app/account/devices');
  await expect(page.getByRole('heading', { name: 'Devices and browsers' })).toBeVisible();
  await expect(page.getByRole('main')).toHaveCount(1);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
  await assertNoHorizontalOverflow(page);
});

test('device inventory renders one bounded window and reports the true total', async ({ page }) => {
  const inventoryRequestUrls: URL[] = [];
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: {
    devices: [{ ...device, inventory_count: 5000, inventory_observed: '2026-08-09T12:00:00' }],
    limit: 100, offset: 0, total: 1,
  } }));
  await page.route('**/api/annotations/devices/device-1/inventory?*', (route) => {
    inventoryRequestUrls.push(new URL(route.request().url()));
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
  expect(inventoryRequestUrls).toHaveLength(1);
  expect(inventoryRequestUrls[0].searchParams.get('limit')).toBe('200');
  expect(inventoryRequestUrls[0].searchParams.get('offset')).toBe('0');
  await inventory.getByRole('button', { name: 'Next' }).click();
  await expect.poll(() => inventoryRequestUrls[inventoryRequestUrls.length - 1]
    ?.searchParams.get('offset')).toBe('200');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((violation) => (
    ['critical', 'serious'].includes(violation.impact || '')
  ))).toEqual([]);
});

test('account summary makes the e-reader manager discoverable', async ({ page }) => {
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices: [{ ...device, annotation_count: 1 }, { ...device, public_id: 'browser-1', label: 'Browser', type: 'webreader',
      origin_annotation_count: 1, annotation_count: 0 }], total: 2, limit: 100, offset: 0,
  } }));
  await page.goto('/app/account');
  const card = page.getByRole('region', { name: 'Devices and browsers' });
  const physical = card.getByRole('listitem').filter({ hasText: 'Libra Colour' });
  await expect(physical).toContainText('1 annotation assigned to this source');
  const browser = card.getByRole('listitem').filter({ hasText: 'Browser' });
  await expect(browser).toContainText('1 annotation from this source');
  await expect(card.getByRole('link')).toHaveCount(2);
  await card.getByRole('link', { name: 'Manage devices and browsers' }).click();
  await expect(page).toHaveURL(/\/app\/account\/devices$/);
  await expect(page.getByRole('heading', { name: 'Devices and browsers', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Browser', exact: true })).toBeVisible();
});

test('device manager owns pairing instead of sending users to the classic account page', async ({ page }) => {
  await stubDevices(page);
  await page.goto('/app/account/devices');
  await expect(page.getByRole('heading', { name: 'Pair a Kobo or KOReader' })).toBeVisible();
  await expect(page.getByText('Manage your Kobo sync URL in the classic account page.')).toHaveCount(0);
  await expect(page.locator('a[href="/me"]')).toHaveCount(0);
});
