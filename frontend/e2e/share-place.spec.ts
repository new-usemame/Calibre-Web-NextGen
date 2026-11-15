import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';

// Share my place (spec state/kindle-kids/specs/2026-10-11-share-my-place.md,
// criteria 14-15 and §10): one checkbox per device that can honor it, saved
// with PATCH /api/annotations/devices/<id> {share_place}, shown only while the
// server's share_place_gates switch is on. The device list and the PATCH are
// stubbed so the spec pins the page's contract, not server state.

const kobo = {
  public_id: 'device-1', label: 'Libra Colour', type: 'kobo', model: 'Kobo Libra Colour',
  firmware: '4.45.23684', first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-09T12:00:00',
  annotation_count: 3, origin_annotation_count: 3, inventory_count: 0, inventory_observed: null,
  storage_free: null, storage_total: null, storage_observed: null, active: true, share_place: false,
  place_control: 'per_device',
};
const browser = {
  ...kobo, public_id: 'browser-1', type: 'webreader', kind: 'webreader', label: 'Browser',
  model: 'CWNG web reader', browser_identity: 'account',
};
// Stock KOReader (never sent X-CWNG-Device-ID): the server cannot tell its
// requests apart, so it always shares and offers no checkbox.
const koreader = {
  ...kobo, public_id: 'koreader-1', type: 'koreader', kind: 'koreader', label: 'Clara KOReader',
  model: 'KOReader', place_control: 'always_shared',
};
const HELP = 'Devices with this on open each book where you last stopped on any of them. Turn it off for a device someone else reads on.';
const PLUGIN_NOTE = 'Always shares its place. Install or update the NextGen Sync plugin on this device to choose.';

async function stubDevices(page: Page, patch: (body: unknown) => Promise<{ status: number; json: unknown }>,
  gates = true) {
  const devices = [{ ...kobo }, { ...koreader }, { ...browser }];
  const sent: { id: string; body: unknown }[] = [];
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices, total: devices.length, limit: 100, offset: 0, share_place_gates: gates,
  } }));
  await page.route(/\/api\/annotations\/devices\/[^/?]+$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue();
    const id = route.request().url().split('/').pop()!;
    const body = route.request().postDataJSON();
    sent.push({ id, body });
    const reply = await patch(body);
    if (reply.status === 200) {
      const device = devices.find(item => item.public_id === id)!;
      Object.assign(device, reply.json);
    }
    await route.fulfill({ status: reply.status, json: reply.json });
  });
  return sent;
}

const shareBox = (page: Page, name: string) =>
  page.getByRole('checkbox', { name: `Share my place ${name}`, exact: true });

test('Share my place saves per device and shows the shared help once', async ({ page }) => {
  const sent = await stubDevices(page, async body => ({
    status: 200, json: { ...kobo, share_place: (body as { share_place: boolean }).share_place },
  }));
  await page.goto('/app/account/devices');
  const libra = shareBox(page, 'Libra Colour');
  const web = shareBox(page, 'Web reader');
  await expect(page.getByRole('link', { name: 'Web reader', exact: true })).toBeVisible();
  await expect(libra).not.toBeChecked();
  await expect(web).not.toBeChecked();
  await expect(page.getByText(HELP, { exact: true })).toHaveCount(1);
  await expect(libra).toHaveAccessibleDescription(HELP);
  // Exactly the two per_device rows get a box; stock KOReader gets the note instead.
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  const clara = page.getByRole('listitem').filter({ has: page.getByRole('link', { name: 'Clara KOReader', exact: true }) });
  await expect(clara.getByRole('checkbox')).toHaveCount(0);
  await expect(clara.getByText(PLUGIN_NOTE, { exact: true })).toBeVisible();

  await libra.focus();
  await page.keyboard.press('Space');
  await expect(libra).toBeChecked();
  await expect.poll(() => sent).toEqual([{ id: 'device-1', body: { share_place: true } }]);
  await expect(libra).toBeEnabled();
  await expect(web).not.toBeChecked();

  await libra.click();
  await expect(libra).not.toBeChecked();
  await expect.poll(() => sent[sent.length - 1]).toEqual({ id: 'device-1', body: { share_place: false } });
  await expect(page.getByRole('alert').filter({ hasText: 'Share my place' })).toHaveCount(0);

  await assertNoHorizontalOverflow(page);
  const scan = await new AxeBuilder({ page }).include('main').analyze();
  expect(scan.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
});

test('a failed Share my place save reverts the box and says so', async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const sent = await stubDevices(page, async () => {
    await held;
    return { status: 500, json: { error: 'internal' } };
  });
  await page.goto('/app/account/devices');
  const web = shareBox(page, 'Web reader');
  await web.click();
  // Immediate: the box flips while the save is in flight and cannot be re-sent.
  await expect(web).toBeChecked();
  await expect(web).toBeDisabled();
  release();
  await expect(web).not.toBeChecked();
  await expect(web).toBeEnabled();
  const alert = page.getByRole('alert').filter({ hasText: 'Could not save Share my place. Try again.' });
  await expect(alert).toBeVisible();
  await expect(alert).toHaveCount(1);
  expect(sent).toEqual([{ id: 'browser-1', body: { share_place: true } }]);
  await expect(shareBox(page, 'Libra Colour')).not.toBeChecked();
});

test('with the server switch off the page shows no Share my place controls', async ({ page }) => {
  const sent = await stubDevices(page, async () => ({ status: 500, json: {} }), false);
  await page.goto('/app/account/devices');
  await expect(page.getByRole('link', { name: 'Libra Colour', exact: true })).toBeVisible();
  // The account Browser keeps today's name while the switch is off.
  await expect(page.getByRole('link', { name: 'Browser', exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox')).toHaveCount(0);
  await expect(page.getByText(HELP, { exact: true })).toHaveCount(0);
  await expect(page.getByText(PLUGIN_NOTE, { exact: true })).toHaveCount(0);
  expect(sent).toEqual([]);
});
