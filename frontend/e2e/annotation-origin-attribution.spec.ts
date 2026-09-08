import { test, expect, type Page } from '@playwright/test';

/* #2075. A KOReader that syncs highlights registers a device and sets
 * `origin_device_id`; the page used to read only `assigned_device_id`, so a
 * freshly synced highlight said "Unknown device", and assigning it to a device
 * the book had not seen before said "Assigned to Deleted device" — over a write
 * that had succeeded. Both are display-and-wiring defects, invisible to the
 * pure-function lane that covers the resolver, so they are driven here.
 *
 * The book-scoped `devices` map deliberately names ONLY the origin device:
 * that is the payload shape that produced the reporter's "Deleted device". */

const boox = { public_id: 'boox-a', label: 'Boox Tablet', type: 'koreader', model: 'Boox Tab Ultra', active: true };
const phone = { public_id: 'phone-b', label: 'Phone B', type: 'koreader', model: 'Pixel 9', active: true };

async function stubSyncedHighlight(page: Page) {
  /* Every call this page makes is stubbed, so the assertions are about the
   * page's own resolution and not about the fixture library's contents. */
  await page.route('**/api/v1/auth/csrf', (route) => route.fulfill({ json: { csrf_token: 'e2e-token' } }));
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: {
    name: 'e2e', role: { anonymous: false, admin: false }, features: {}, locale: 'en',
  } }));
  await page.route('**/api/v1/books/2', (route) => route.fulfill({ json: { id: 2, title: 'A Book' } }));
  await page.route('**/annotations/2/data.json', (route) => route.fulfill({ json: {
    annotation_count: 1,
    annotations: [{
      annotation_id: 'ann-1', highlighted_text: 'A passage synced from KOReader',
      highlight_color: 'yellow', note_text: null, chapter_progress: 0.5, source: 'koreader',
      position_type: null, origin_device_id: 'boox-a', assigned_device_id: null, anchor_status: 'ok',
    }],
    devices: { 'boox-a': { label: boox.label, model: boox.model, type: boox.type } },
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: { devices: [boox, phone] } }));
  await page.route('**/annotations/2/ann-1', (route) => route.fulfill({ json: { annotation_id: 'ann-1' } }));
}

test('a synced highlight is attributed to its origin device, and reassigning it names the real device', async ({ page }, testInfo) => {
  await stubSyncedHighlight(page);
  await page.goto('/app/book/2/annotations');

  const row = page.locator('[data-virtual-row]').first();
  await expect(row).toBeVisible();
  const select = row.locator('select');

  // The origin is what the row falls back to, and it is named rather than
  // reported as unknown. Asserting the CHECKED option, not the option list:
  // every device appears as an option either way.
  await expect(select.locator('option:checked')).toHaveText(/Use original device: Boox Tablet/);
  if (testInfo.project.name === 'mobile') {
    await expect(page.locator('label').filter({ hasText: /^Device/ }).first().locator('select')).toContainText('Boox Tablet (1)');
  } else {
    await expect(page.getByRole('radio', { name: /Boox Tablet, 1 highlights and notes/ })).toBeVisible();
    await expect(page.getByRole('radio', { name: /Unknown device/ })).toHaveCount(0);
  }

  // Assigning to a device the book-scoped map has never seen must name it.
  await select.selectOption('phone-b');
  const toast = page.locator('div[role="status"][class*="toast"]');
  await expect(toast).toContainText('Assigned to Phone B.');
  await expect(toast).not.toContainText('Deleted device');

  // Dismiss ends the message without reverting the assignment — Undo used to
  // be the only way out of a message that was already wrong.
  await toast.getByRole('button', { name: 'Dismiss' }).click();
  await expect(toast).toHaveCount(0);
  await expect(select).toHaveValue('phone-b');
});
