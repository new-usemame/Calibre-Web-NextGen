import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

// Grade populated controls and the expanded shelf list in both palettes.
// API responses are local fixtures; no real device setting is changed.
test.use({ contextOptions: { reducedMotion: 'reduce' } });

test('book choice supports keyboard selection and accessible desktop/mobile palettes', async ({ page }, testInfo) => {
  const scope = {
    mode: 'unset', shelf_ids: [], magic_shelf_ids: [], account_mode: 'shelves', effective_mode: 'shelves',
    fallback: null, choices: { my_library: true,
      shelves: [{ id: 11, name: 'To Read' }, { id: 12, name: 'Reader shelf' }],
      magic_shelves: [{ id: 13, name: 'Recently added' }] },
  };
  const base = { firmware: null, first_seen: '2026-08-01T12:00:00', last_seen: '2026-10-09T12:00:00',
    annotation_count: 0, origin_annotation_count: 0, inventory_count: 0, inventory_observed: null,
    storage_free: null, storage_total: null, storage_observed: null, active: true, share_place: false,
    place_control: 'per_device', book_scope: scope };
  const devices = [
    { ...base, public_id: 'reader-a', label: 'KOReader A', type: 'koreader', kind: 'koreader', model: 'Kindle' },
    { ...base, public_id: 'kobo-a', label: 'Kobo A', type: 'kobo', kind: 'kobo', model: 'Kobo Clara' },
  ];
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices, total: devices.length, limit: 100, offset: 0, share_place_gates: true,
  } }));
  await page.goto('/app/account/devices');
  // Both the KOReader and the Kobo device get the control; exercise the first (KOReader A).
  const groups = page.getByRole('group', { name: 'Books on this device' });
  await expect(groups).toHaveCount(2);
  const group = groups.first();
  const radio = group.getByRole('radio', { name: 'Only these shelves' });
  await radio.focus();
  await expect(radio).toBeFocused();
  await page.keyboard.press('Space');
  await expect(radio).toBeChecked();
  const shelf = group.getByRole('checkbox', { name: 'To Read', exact: true });
  await page.keyboard.press('Tab');
  await expect(shelf).toBeFocused();
  await page.keyboard.press('Space');
  await expect(shelf).toBeChecked();
  await expect(group.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  await assertNoHorizontalOverflow(page);
  for (const theme of ['dark', 'light']) {
    await page.evaluate(async value => {
      document.documentElement.setAttribute('data-theme', value);
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    }, theme);
    await page.mouse.move(0, 0);
    const result = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(result.violations.filter(v => ['critical', 'serious'].includes(v.impact || '')),
      `${theme}: ${JSON.stringify(result.violations)}`).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`device-scope-${theme}.jpg`), type: 'jpeg', fullPage: true });
  }
});
