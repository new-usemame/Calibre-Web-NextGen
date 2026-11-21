import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

const label = "Align this Kobo's percentage with CrossPoint";
const box = (page: Page, name = 'Libra Colour') =>
  page.getByRole('checkbox', { name: `${label} ${name}`, exact: true });

async function stubDevices(page: Page,
  patch: (body: { align_percent: boolean }) => Promise<{ status: number; json: unknown }>) {
  const kobo = {
    public_id: 'libra', label: 'Libra Colour', type: 'kobo', kind: 'kobo',
    model: 'Kobo Libra Colour', firmware: null, first_seen: null, last_seen: null,
    annotation_count: 0, inventory_count: 0, inventory_observed: null,
    storage_free: null, storage_total: null, storage_observed: null,
    active: true, share_place: false, align_percent: false,
  };
  const devices = [kobo, { ...kobo, public_id: 'clara', label: 'Clara' },
    { ...kobo, public_id: 'koreader', label: 'KOReader', kind: 'koreader', type: 'koreader' },
    { ...kobo, public_id: 'browser', label: 'Browser', kind: 'webreader', type: 'webreader' }];
  const sent: { id: string; body: unknown }[] = [];
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: {
    id: 7, name: 'Reader', role: { anonymous: false }, features: {}, locale: 'en',
  } }));
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices: route.request().url().includes('active=false') ? [] : devices,
    total: devices.length, limit: 100, offset: 0, share_place_gates: false,
  } }));
  await page.route(/\/api\/annotations\/devices\/[^/?]+$/, async route => {
    const id = route.request().url().split('/').pop()!;
    const body = route.request().postDataJSON();
    sent.push({ id, body });
    const reply = await patch(body);
    if (reply.status === 200) Object.assign(devices.find(device => device.public_id === id)!, reply.json);
    await route.fulfill(reply);
  });
  return sent;
}

test('alignment is Kobo-only, off initially and saved independently of Share my place', async ({ page }) => {
  const sent = await stubDevices(page, async body => ({ status: 200, json: body }));
  await page.goto('/app/account/devices');
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await expect(box(page)).not.toBeChecked();
  await expect(box(page, 'Clara')).not.toBeChecked();
  await box(page).focus();
  await page.keyboard.press('Space');
  await expect(box(page)).toBeChecked();
  await expect(box(page)).toBeEnabled();
  expect(sent).toEqual([{ id: 'libra', body: { align_percent: true } }]);
  await expect(box(page, 'Clara')).not.toBeChecked();
  await page.reload();
  await expect(box(page)).toBeChecked();
  await box(page).click();
  await expect(box(page)).not.toBeChecked();
  await expect(box(page)).toBeEnabled();
  expect(sent[1]).toEqual({ id: 'libra', body: { align_percent: false } });
  await assertNoHorizontalOverflow(page);
  const scan = await new AxeBuilder({ page }).include('main').analyze();
  expect(scan.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
});

test('a failed alignment save restores the checkbox and reports the error on that Kobo', async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const sent = await stubDevices(page, async () => {
    await held;
    return { status: 503, json: { error: 'internal' } };
  });
  try {
    await page.goto('/app/account/devices');
    await box(page).click();
    await expect(box(page)).toBeChecked();
    await expect(box(page)).toBeDisabled();
    release();
    await expect(box(page)).not.toBeChecked();
    await expect(box(page)).toBeEnabled();
    const alert = page.getByRole('alert').filter({ hasText: 'Could not save the percentage setting. Try again.' });
    await expect(alert).toBeVisible();
    await expect(alert).toHaveCount(1);
    await expect(box(page)).toHaveAccessibleDescription(/Could not save the percentage setting/);
    await expect(box(page, 'Clara')).not.toBeChecked();
    expect(sent).toEqual([{ id: 'libra', body: { align_percent: true } }]);
  } finally { release(); }
});
