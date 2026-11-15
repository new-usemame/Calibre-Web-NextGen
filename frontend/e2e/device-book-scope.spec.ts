import { test, expect, type Page } from '@playwright/test';
import { assertNoHorizontalOverflow } from './utils';

// Per-device book scope (spec state/kindle-kids/specs/2026-10-12-per-device-scope.md,
// "UI: Devices page"; contracts CONTRACT.md for PR A and
// state/kindle-kids/legs/device-scope-b/CONTRACT.md for PR B). Each e-reader row,
// KOReader and (from PR B, when Kobo sync honours it) Kobo, gets "Books on this
// device": the account setting (default), everything in my library, My Library
// (when on), or only chosen shelves. The list and the PATCH are stubbed so the spec
// pins the page's contract, not server state. A row without `book_scope` (an older
// server, the web reader) gets no control.

const SHELVES = [{ id: 11, name: 'To Read' }, { id: 12, name: 'Kindle' }];
const HELP = 'Choose which books this device keeps. Books you take off are removed from the device at its next sync; your place, highlights and notes stay saved.';
const DELETED = 'The shelf chosen for this device was deleted. It uses your account setting until you choose again.';
const UNAVAILABLE = 'The shelf chosen for this device is no longer available. It uses your account setting until you choose again.';

type Scope = {
  mode: string; shelf_ids: number[]; magic_shelf_ids: number[];
  account_mode: string; effective_mode: string;
  fallback: null | { reason: string };
  choices: { shelves: typeof SHELVES; magic_shelves: { id: number; name: string }[]; my_library: boolean };
};

const unset = (over: Partial<Scope> = {}): Scope => ({
  mode: 'unset', shelf_ids: [], magic_shelf_ids: [], account_mode: 'shelves', effective_mode: 'shelves',
  fallback: null, choices: { shelves: SHELVES, magic_shelves: [], my_library: false }, ...over,
});

const base = {
  firmware: null, first_seen: '2026-08-01T12:00:00', last_seen: '2026-10-09T12:00:00',
  annotation_count: 0, origin_annotation_count: 0, inventory_count: 0, inventory_observed: null,
  storage_free: null, storage_total: null, storage_observed: null, active: true,
  share_place: false, place_control: 'per_device',
};
const kindle = { ...base, public_id: 'kindle-1', label: 'Kindle A', type: 'koreader', kind: 'koreader', model: 'Kindle' };
const kobo = { ...base, public_id: 'kobo-1', label: 'Kobo A', type: 'kobo', kind: 'kobo', model: 'Kobo Clara' };
const browser = {
  ...base, public_id: 'browser-1', label: 'Browser', type: 'webreader', kind: 'webreader',
  model: 'CWNG web reader', browser_identity: 'account', book_scope: null,
};

async function stubDevices(page: Page, scopes: Record<string, Scope | undefined>) {
  const devices = [
    { ...kindle, book_scope: scopes['kindle-1'] },
    { ...kobo, book_scope: scopes['kobo-1'] },
    { ...browser },
  ];
  const sent: { id: string; body: any }[] = [];
  await page.route('**/api/annotations/devices?*', route => route.fulfill({ json: {
    devices, total: devices.length, limit: 100, offset: 0, share_place_gates: true,
  } }));
  await page.route(/\/api\/annotations\/devices\/[^/?]+$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue();
    const id = route.request().url().split('/').pop()!;
    const body = route.request().postDataJSON();
    sent.push({ id, body });
    const device = devices.find(item => item.public_id === id)!;
    const scope = body.book_scope;
    device.book_scope = {
      ...(device.book_scope as Scope), ...scope,
      shelf_ids: scope.shelf_ids ?? [], magic_shelf_ids: scope.magic_shelf_ids ?? [],
      effective_mode: scope.mode === 'unset' ? 'shelves' : scope.mode, fallback: null,
    };
    await route.fulfill({ status: 200, json: device });
  });
  return sent;
}

const row = (page: Page, label: string) =>
  page.getByRole('listitem').filter({ has: page.getByRole('link', { name: label, exact: true }) });
const control = (page: Page, label: string) =>
  row(page, label).getByRole('group', { name: /Books on this device/ });

test('a device starts on the account setting and saves a shelf choice', async ({ page }) => {
  const sent = await stubDevices(page, { 'kindle-1': unset(), 'kobo-1': unset() });
  await page.goto('/app/account/devices');

  const books = control(page, 'Kindle A');
  await expect(books).toBeVisible();
  await expect(books.getByRole('radio', { name: 'Same as my account setting' })).toBeChecked();
  await expect(books.getByRole('radio', { name: 'Everything in my library' })).not.toBeChecked();
  await expect(books.getByRole('radio', { name: 'Only these shelves' })).not.toBeChecked();
  // My Library is off for this account, so it is not offered.
  await expect(books.getByRole('radio')).toHaveCount(3);
  await expect(page.getByText(HELP, { exact: true }).first()).toBeVisible();

  await books.getByRole('radio', { name: 'Only these shelves' }).check();
  await books.getByRole('checkbox', { name: 'Kindle', exact: true }).check();
  await books.getByRole('button', { name: 'Save' }).click();

  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].id).toBe('kindle-1');
  expect(sent[0].body.book_scope.mode).toBe('shelves');
  expect(sent[0].body.book_scope.shelf_ids).toEqual([12]);
  // Only the Kindle was saved; the Kobo row keeps its own, untouched choice.
  expect(sent.every(item => item.id === 'kindle-1')).toBe(true);
  await expect(control(page, 'Kobo A').getByRole('radio', { name: 'Same as my account setting' }))
    .toBeChecked();

  // A reload shows what was saved.
  await page.reload();
  const again = control(page, 'Kindle A');
  await expect(again.getByRole('radio', { name: 'Only these shelves' })).toBeChecked();
  await expect(again.getByRole('checkbox', { name: 'Kindle', exact: true })).toBeChecked();
  await expect(again.getByRole('checkbox', { name: 'To Read', exact: true })).not.toBeChecked();
});

test('a shelf choice cannot be saved with no shelf ticked', async ({ page }) => {
  const sent = await stubDevices(page, { 'kindle-1': unset(), 'kobo-1': unset() });
  await page.goto('/app/account/devices');

  const books = control(page, 'Kindle A');
  await books.getByRole('radio', { name: 'Only these shelves' }).check();
  const save = books.getByRole('button', { name: 'Save' });
  if (await save.isEnabled()) await save.click();

  await page.waitForTimeout(300);
  expect(sent).toEqual([]);
});

test('going back to the account setting sends unset', async ({ page }) => {
  const sent = await stubDevices(page, {
    'kindle-1': unset({ mode: 'library', effective_mode: 'library' }), 'kobo-1': unset(),
  });
  await page.goto('/app/account/devices');

  const books = control(page, 'Kindle A');
  await expect(books.getByRole('radio', { name: 'Everything in my library' })).toBeChecked();
  await books.getByRole('radio', { name: 'Same as my account setting' }).check();
  await books.getByRole('button', { name: 'Save' }).click();

  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].body.book_scope.mode).toBe('unset');
});

test('My Library is offered when it is on', async ({ page }) => {
  await stubDevices(page, {
    'kindle-1': unset({ choices: { shelves: SHELVES, magic_shelves: [], my_library: true } }),
    'kobo-1': unset(),
  });
  await page.goto('/app/account/devices');

  await expect(control(page, 'Kindle A').getByRole('radio')).toHaveCount(4);
});

test('a device whose shelf was deleted or is unavailable says so, Kobo included', async ({ page }) => {
  await stubDevices(page, {
    'kindle-1': unset({ mode: 'shelves', shelf_ids: [99], fallback: { reason: 'shelf_deleted' } }),
    'kobo-1': unset({ mode: 'shelves', shelf_ids: [98], fallback: { reason: 'shelf_unavailable' } }),
  });
  await page.goto('/app/account/devices');

  await expect(row(page, 'Kindle A').getByText(DELETED, { exact: true })).toBeVisible();
  await expect(row(page, 'Kobo A').getByText(UNAVAILABLE, { exact: true })).toBeVisible();
  await expect(row(page, 'Kobo A').getByText(DELETED, { exact: true })).toHaveCount(0);
});

test('a KOReader device whose shelf is no longer available says so', async ({ page }) => {
  await stubDevices(page, {
    'kindle-1': unset({ mode: 'shelves', shelf_ids: [98], fallback: { reason: 'shelf_unavailable' } }),
    'kobo-1': unset(),
  });
  await page.goto('/app/account/devices');

  await expect(row(page, 'Kindle A').getByText(UNAVAILABLE, { exact: true })).toBeVisible();
});

test('a device that kept one of its two shelves shows no fallback notice', async ({ page }) => {
  await stubDevices(page, {
    'kindle-1': unset({ mode: 'shelves', shelf_ids: [12], effective_mode: 'shelves', fallback: null }),
    'kobo-1': unset(),
  });
  await page.goto('/app/account/devices');

  const books = control(page, 'Kindle A');
  await expect(books.getByRole('radio', { name: 'Only these shelves' })).toBeChecked();
  await expect(books.getByRole('checkbox', { name: 'Kindle', exact: true })).toBeChecked();
  await expect(page.getByText(DELETED, { exact: true })).toHaveCount(0);
  await expect(page.getByText(UNAVAILABLE, { exact: true })).toHaveCount(0);
});

test('a Kobo row gets the control and saves its own choice', async ({ page }) => {
  const sent = await stubDevices(page, { 'kindle-1': unset(), 'kobo-1': unset() });
  await page.goto('/app/account/devices');

  await expect(control(page, 'Kindle A')).toBeVisible();
  const books = control(page, 'Kobo A');
  await expect(books).toBeVisible();
  await expect(books.getByRole('radio', { name: 'Same as my account setting' })).toBeChecked();
  await expect(books.getByRole('radio')).toHaveCount(3);
  await expect(row(page, 'Kobo A').getByText(HELP, { exact: true })).toBeVisible();

  await books.getByRole('radio', { name: 'Only these shelves' }).check();
  await books.getByRole('checkbox', { name: 'To Read', exact: true }).check();
  await books.getByRole('button', { name: 'Save' }).click();

  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].id).toBe('kobo-1');
  expect(sent[0].body.book_scope.mode).toBe('shelves');
  expect(sent[0].body.book_scope.shelf_ids).toEqual([11]);
  // The Kindle's choice was not sent.
  await expect(control(page, 'Kindle A').getByRole('radio', { name: 'Same as my account setting' }))
    .toBeChecked();

  await page.reload();
  const again = control(page, 'Kobo A');
  await expect(again.getByRole('radio', { name: 'Only these shelves' })).toBeChecked();
  await expect(again.getByRole('checkbox', { name: 'To Read', exact: true })).toBeChecked();
  await assertNoHorizontalOverflow(page);
});

test('the web reader and a row from an older server get no control', async ({ page }) => {
  await stubDevices(page, { 'kindle-1': undefined, 'kobo-1': undefined });
  await page.goto('/app/account/devices');

  await expect(control(page, 'Kobo A')).toHaveCount(0);
  await expect(control(page, 'Kindle A')).toHaveCount(0);
  await expect(row(page, 'Web reader').getByRole('group', { name: /Books on this device/ })).toHaveCount(0);
  await assertNoHorizontalOverflow(page);
});
