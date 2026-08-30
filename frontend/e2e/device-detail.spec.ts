import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const device = {
  public_id: 'device-1', label: 'Libra Colour', type: 'kobo', kind: 'kobo',
  kind_label: 'Kobo', model: 'Kobo Libra Colour', firmware: '4.45.23684',
  first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-29T12:00:00',
  annotation_count: 3, highlights: 1, notes: 1, dogears: 1,
  inventory_count: 1, inventory_observed: '2026-08-29T12:00:00',
  storage_free: 1024, storage_total: 2048, storage_observed: '2026-08-29T12:00:00',
  seeded_books: 1, unseeded_books: 1,
  authority: {
    unseeded: 1, seeding: 0, authoritative: 1, quarantined: 0, disabled: 0,
    books_partially_seeded: 1,
  },
  active: true,
};

async function stubDeviceDetail(page: Page) {
  const annotationRequests: URL[] = [];
  let releaseNotes = () => {};
  const notesPending = new Promise<void>((resolve) => { releaseNotes = resolve; });
  // Force the detail payload fallback: changing annotation type must not drop
  // the already-resolved device while the next response is pending.
  await page.route('**/api/annotations/devices', (route) => route.fulfill({
    json: { devices: [], limit: 100, offset: 0, total: 0 },
  }));
  await page.route('**/api/annotations/devices/device-1/summary', (route) => route.fulfill({
    json: {
      highlights: 1, notes: 1, dogears: 1, books_with_position: 1,
      last_position_at: '2026-08-29T12:00:00', seeded_books: 1, unseeded_books: 1,
    },
  }));
  await page.route('**/api/annotations/devices/device-1/positions?*', (route) => route.fulfill({
    json: {
      limit: 100, offset: 0, total: 1,
      positions: [{
        book_id: 5, book: { id: 5, title: 'A Test Book' }, progress_percent: 42,
        location_type: 'cfi', location_value: 'epubcfi(/6/2)',
        client_modified_at: '2026-08-29T12:00:00', server_modified_at: '2026-08-29T12:00:00',
      }],
    },
  }));
  await page.route('**/api/annotations/devices/device-1/annotations?*', async (route) => {
    const url = new URL(route.request().url());
    annotationRequests.push(url);
    const type = url.searchParams.get('type') || 'highlight';
    const text = type === 'highlight' ? 'A highlighted passage' : null;
    const note = type === 'note' ? 'A standalone note' : null;
    if (type === 'note') await notesPending;
    await route.fulfill({ json: {
      device,
      annotations: [{
        annotation_id: `${type}-1`, book_id: 5, annotation_type: type,
        highlighted_text: text, highlight_color: 'yellow', note_text: note,
        chapter_progress: 0.42, source: 'kobo', created_at: '2026-08-29T12:00:00',
        origin_device_id: 'device-1', assigned_device_id: 'device-1',
        book: { id: 5, title: 'A Test Book' },
      }],
      devices: { 'device-1': { label: device.label, model: device.model, type: device.type } },
      page: 1, pages: 1, page_size: 50, total: 1,
      role: url.searchParams.get('role'), type,
    } });
  });
  await page.route('**/api/annotations/devices/device-1/inventory?*', (route) => route.fulfill({
    json: {
      observed_at: '2026-08-29T12:00:00', limit: 200, offset: 0, total: 1,
      books: [{
        inventory_item_id: 9, book_id: 5, lpath: 'Books/A Test Book.epub',
        checksum: '1'.repeat(32), size: 100, mtime: 1,
      }],
    },
  }));
  return { requests: annotationRequests, releaseNotes };
}

test('device detail exposes typed tabs, assignment view, inventory, and positions', async ({ page }) => {
  const { requests, releaseNotes } = await stubDeviceDetail(page);
  await page.goto('/app/account/devices/device-1');
  await expect(page.getByRole('heading', { name: 'Libra Colour' })).toBeVisible();
  await expect(page.getByText('A highlighted passage')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Reading positions' })).toBeVisible();
  await expect(page.getByText('42% read')).toBeVisible();

  const highlights = page.getByRole('tab', { name: 'Highlights' });
  const notes = page.getByRole('tab', { name: 'Notes' });
  await highlights.focus();
  await highlights.press('ArrowRight');
  await expect(notes).toBeFocused();
  releaseNotes();
  await expect(page.getByText('A standalone note')).toBeVisible();
  await expect(notes).toBeFocused();

  await page.getByRole('checkbox', { name: 'Show annotations assigned to this device' }).check();
  await expect.poll(() => requests.at(-1)?.searchParams.get('role')).toBe('assigned');

  await notes.press('End');
  await expect(page.getByRole('tab', { name: 'Device library' })).toBeFocused();
  await expect(page.getByRole('status').filter({ hasText: 'Showing' })).toContainText('Showing 1 of 1 books');

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((violation) => (
    ['critical', 'serious'].includes(violation.impact || '')
  ))).toEqual([]);
});

test('admin device board reuses the device summaries', async ({ page }) => {
  await page.route('**/api/admin/devices*', (route) => route.fulfill({ json: {
    devices: [{ ...device, user: { id: 7, name: 'e2e' } }],
    limit: 50, offset: 0, total: 1,
  } }));
  await page.goto('/app/admin/devices');
  await expect(page.getByRole('heading', { name: 'Device administration' })).toBeVisible();
  const card = page.getByTestId('admin-device-list').getByRole('listitem')
    .filter({ hasText: 'Libra Colour' });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('Account: e2e');
  await expect(card).toContainText('Partially seeded books');
});
