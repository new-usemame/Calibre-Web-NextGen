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
    anchor_status: index === 0 ? 'unresolved' : 'ok',
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
    await expect(page.getByRole('radio', { name: /Unknown device, 595 highlights and notes/ })).toBeVisible();
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
  await expect(page.getByText('Not in current file', { exact: true })).toBeVisible();
  await expect(page.getByLabel("Warning: this highlight can’t be shown in the book")).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Device: unknown' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Import and export' }).click();
  await expect(page.getByRole('link', { name: 'Markdown' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Import', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Select' }).click();
  const checkbox = page.getByRole('checkbox', { name: /Select highlight: Highlight 0/ });
  await checkbox.check();
  await assertNoHorizontalOverflow(page);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
});

test('many device filters collapse and touch long-press enters selection', async ({ page }, testInfo) => {
  const devices = Array.from({ length: 8 }, (_, index) => ({
    ...libra, public_id: `device-${index}`, label: `Reader ${index}`,
  }));
  await stubAnnotations(page, 20, devices);
  await page.goto('/app/book/2/annotations');
  await expect(page.getByRole('radiogroup', { name: 'Filter by device' })).toBeHidden();
  await expect(page.locator('label').filter({ hasText: /^Device/ }).first().locator('select')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const firstRow = page.getByRole('listitem').filter({ hasText: 'Highlight 0' });
  const rowBody = firstRow.locator('div').first();
  if (testInfo.project.name === 'mobile') {
    await rowBody.evaluate((element) => element.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, pointerType: 'touch', pointerId: 1, isPrimary: true,
    })));
  } else {
    const box = await firstRow.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + 20, box!.y + 20);
    await page.mouse.down();
  }
  await expect(page.getByText('1 selected', { exact: true })).toBeVisible();
  if (testInfo.project.name === 'mobile') {
    await rowBody.evaluate((element) => element.dispatchEvent(new PointerEvent('pointerup', {
      bubbles: true, pointerType: 'touch', pointerId: 1, isPrimary: true,
    })));
  } else await page.mouse.up();
  await expect(page.getByRole('button', { name: 'Select all 20' })).toBeFocused();
});

/*
 * #1544 fixed this on the plain list; this page was rewritten to a virtualized
 * list on a branch at the same time, so the two changes MERGED WITHOUT A
 * CONFLICT and the fix had to be re-grafted onto the new renderer by hand. A
 * hand-graft nothing exercises is exactly what the clean merge already hid
 * once, so pin the behaviour here rather than trusting the graft.
 */
test('a standalone note is drawn as a note, not as a highlight that lost its text', async ({ page }) => {
  const annotations = [
    { annotation_id: 'ann-note', highlighted_text: '', highlight_color: null, position_type: 'unanchored',
      note_text: 'The middle section drags.', chapter_progress: null, source: 'webreader',
      origin_device_id: null, assigned_device_id: null, anchor_status: 'unanchored' },
    { annotation_id: 'ann-quote', highlighted_text: 'A real passage.', highlight_color: 'yellow', position_type: null,
      note_text: null, chapter_progress: 0.5, source: 'kobo',
      origin_device_id: null, assigned_device_id: null, anchor_status: 'ok' },
  ];
  await page.route('**/annotations/2/data.json', (route) => route.fulfill({ json: {
    annotations, annotation_count: 2, devices: {},
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: { devices: [libra] } }));
  await page.goto('/app/book/2/annotations');

  const note = page.getByRole('listitem').filter({ hasText: 'The middle section drags.' });
  await expect(note).toBeVisible();
  // An unguarded render puts a colour swatch beside an empty quote. The API
  // used to make this worse by projecting a default "yellow" onto a row with no
  // colour, so the swatch was even announced as "Yellow"; it no longer does
  // (F-5769c9), but the guard is what keeps the note drawn as a note.
  await expect(note.getByRole('img')).toHaveCount(0);
  await expect(note.locator('blockquote')).toHaveCount(0);
  // 'unanchored' is not 'unresolved': nothing failed to resolve, so the row must
  // not claim the book can't show it.
  await expect(note).not.toContainText('Not in current file');

  // The ordinary highlight in the same list is unaffected — a guard that hid the
  // swatch for everything would pass every assertion above.
  const quote = page.getByRole('listitem').filter({ hasText: 'A real passage.' });
  await expect(quote.getByRole('img')).toHaveCount(1);
  await expect(quote.locator('blockquote')).toHaveText('A real passage.');

  // The accessibility tree must agree with the screen: a screen-reader user is
  // otherwise the only one still told this row is a highlight.
  await page.getByRole('button', { name: 'Select' }).click();
  await expect(page.getByLabel('Select note: The middle section drags.')).toBeVisible();
  await expect(page.getByLabel('Select highlight: A real passage.')).toBeVisible();
});
