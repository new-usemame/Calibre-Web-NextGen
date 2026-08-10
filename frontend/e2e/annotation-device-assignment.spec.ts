import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const libra = { public_id: 'libra', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour', firmware: '4.45', first_seen: null, last_seen: null, annotation_count: 0, active: true };

async function stubAnnotations(page: Page, count = 595) {
  const annotations = Array.from({ length: count }, (_, index) => ({
    annotation_id: `ann-${index}`, highlighted_text: `Highlight ${index}`, highlight_color: 'yellow',
    note_text: index % 3 === 0 ? `Note ${index}` : null, chapter_progress: index / count,
    source: 'kobo', origin_device_id: null, assigned_device_id: null,
  }));
  await page.route('**/annotations/2/data.json', (route) => route.fulfill({ json: {
    annotations, annotation_count: count, devices: { libra: { label: 'Libra Colour', model: 'Kobo Libra Colour', type: 'kobo' } },
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: { devices: [libra] } }));
  const bulkSizes: number[] = [];
  await page.route('**/api/annotations/assignments/bulk', async (route) => {
    const body = await route.request().postDataJSON();
    bulkSizes.push(body.items.length);
    await route.fulfill({ json: { results: body.items.map((item: { annotation_id: string }, index: number) =>
      ({ annotation_id: item.annotation_id, ok: !(bulkSizes.length === 2 && index === 0), ...(bulkSizes.length === 2 && index === 0 ? { error_code: 'revision_conflict' } : {}) })) } });
  });
  return bulkSizes;
}

test('595 unknown highlights virtualize, filter, and bulk assign in 500-item chunks with partial failure', async ({ page }) => {
  const bulkSizes = await stubAnnotations(page);
  await page.goto('/app/book/2/annotations');
  await expect(page.getByRole('radio', { name: /Unknown device, 595 highlights/ })).toBeVisible();
  expect(await page.locator('[data-virtual-row]').count()).toBeLessThan(60);
  await page.getByLabel('Group by').selectOption('device');
  await expect(page.getByRole('listitem').filter({ hasText: 'Unknown device' }).first()).toBeVisible();
  await page.getByLabel('Group by').selectOption('book');
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select all 595' }).click();
  await page.getByRole('combobox', { name: 'Assign selected to device' }).selectOption('libra');
  await expect(page.getByText('594 of 595 assigned to Libra Colour.')).toBeVisible();
  await expect(page.getByText('1 failed.')).toBeVisible();
  expect(bulkSizes).toEqual([500, 95]);
  await expect(page.getByText('1 selected')).toBeVisible();
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
