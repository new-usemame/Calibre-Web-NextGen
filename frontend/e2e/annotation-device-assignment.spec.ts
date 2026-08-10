import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const libra = { public_id: 'libra', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour', firmware: '4.45', first_seen: null, last_seen: null, annotation_count: 0, active: true };

async function stubAnnotations(page: Page, count = 595, availableDevices = [libra]) {
  const annotations = Array.from({ length: count }, (_, index) => ({
    annotation_id: `ann-${index}`, highlighted_text: `Highlight ${index}`, highlight_color: 'yellow',
    note_text: index % 3 === 0 ? `Note ${index}` : null, chapter_progress: index / count,
    source: 'kobo', origin_device_id: null,
    assigned_device_id: availableDevices.length > 1 ? availableDevices[index % availableDevices.length].public_id : null,
  }));
  await page.route('**/annotations/2/data.json', (route) => route.fulfill({ json: {
    annotations, annotation_count: count,
    devices: Object.fromEntries(availableDevices.map((device) => [device.public_id, { label: device.label, model: device.model, type: device.type }])),
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: { devices: availableDevices } }));
  const bulkSizes: number[] = [];
  await page.route('**/api/annotations/assignments/bulk', async (route) => {
    const body = await route.request().postDataJSON();
    bulkSizes.push(body.items.length);
    await route.fulfill({ json: { results: body.items.map((item: { annotation_id: string }, index: number) =>
      ({ annotation_id: item.annotation_id, ok: !(bulkSizes.length === 2 && index === 0), ...(bulkSizes.length === 2 && index === 0 ? { error_code: 'revision_conflict' } : {}) })) } });
  });
  return bulkSizes;
}

test('595 unknown highlights virtualize, filter, and bulk assign in 500-item chunks with partial failure', async ({ page }, testInfo) => {
  const bulkSizes = await stubAnnotations(page);
  await page.goto('/app/book/2/annotations');
  if (testInfo.project.name === 'mobile') {
    await expect(page.locator('label').filter({ hasText: /^Device/ }).first().locator('select')).toContainText('Unknown device (595)');
  } else {
    await expect(page.getByRole('radio', { name: /Unknown device, 595 highlights/ })).toBeVisible();
  }
  expect(await page.locator('[data-virtual-row]').count()).toBeLessThan(60);
  await page.getByLabel('Group by').selectOption('device');
  await expect(page.getByRole('listitem').filter({ hasText: 'Unknown device' }).first()).toBeVisible();
  await page.getByLabel('Group by').selectOption('book');
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select all 595' }).click();
  await page.getByRole('combobox', { name: 'Assign selected to device' }).selectOption('libra');
  await expect(page.getByText('594 of 595 assigned to Libra Colour.')).toBeVisible();
  await expect(page.locator('#main').getByText('1 failed.', { exact: true })).toBeVisible();
  expect(bulkSizes).toEqual([500, 95]);
  await expect(page.getByText('1 selected', { exact: true })).toBeVisible();
});

test('highlight assignment is keyboard named, mobile-safe, and axe-clean', async ({ page }, testInfo) => {
  await stubAnnotations(page, 20);
  if (testInfo.project.name === 'desktop') await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app/book/2/annotations');
  await expect(page.getByRole('combobox', { name: 'Device: unknown' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Select' }).click();
  const checkbox = page.getByRole('checkbox', { name: /Select highlight: Highlight 0/ });
  await checkbox.check();
  await assertNoHorizontalOverflow(page);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
});

test('many device filters collapse and touch long-press enters selection', async ({ page }) => {
  const devices = Array.from({ length: 8 }, (_, index) => ({
    ...libra, public_id: `device-${index}`, label: `Reader ${index}`,
  }));
  await stubAnnotations(page, 20, devices);
  await page.goto('/app/book/2/annotations');
  await expect(page.getByRole('radiogroup', { name: 'Filter by device' })).toBeHidden();
  await expect(page.locator('label').filter({ hasText: /^Device/ }).first().locator('select')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const firstRow = page.getByRole('listitem').filter({ hasText: 'Highlight 0' });
  const box = await firstRow.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + 20, box!.y + 20);
  await page.mouse.down();
  await expect(page.getByText('1 selected', { exact: true })).toBeVisible();
  await page.mouse.up();
  await expect(page.getByRole('button', { name: 'Select all 20' })).toBeFocused();
});
