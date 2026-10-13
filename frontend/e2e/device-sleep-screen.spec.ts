import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const reader = {
  public_id: 'sleep-reader-1',
  label: 'Bedroom Kindle',
  type: 'koreader',
  kind: 'koreader',
  kind_label: 'KOReader',
  model: 'Kindle Paperwhite',
  firmware: '5.19.4',
  first_seen: '2026-10-01T12:00:00',
  last_seen: '2026-10-12T12:00:00',
  annotation_count: 0,
  highlights: 0,
  notes: 0,
  dogears: 0,
  inventory_count: 0,
  inventory_observed: null,
  storage_free: null,
  storage_total: null,
  storage_observed: null,
  active: true,
};
const nativeKobo = { ...reader, public_id: 'native-kobo-1', label: 'Native Kobo', type: 'kobo', kind: 'kobo' };

const me = {
  id: 702,
  name: 'sleep-screen-reader',
  locale: 'en',
  theme: 'dark',
  role: { anonymous: false, admin: false, edit: true, download: true },
  instance_name: 'CWNG test',
  features: {},
};

type SleepScreenMetadata = {
  desired_revision: string;
  state: 'none' | 'waiting_for_device' | 'image' | 'remove';
  support: 'unknown' | 'supported' | 'unsupported';
  platform: string | null;
  plugin_version: string | null;
  screen_width: number | null;
  screen_height: number | null;
  observed_at: string | null;
  applied_revision: string | null;
  applied_status: 'applied' | 'removed' | null;
  applied_at: string | null;
  last_error: string | null;
  preview_url: string | null;
};

const emptyScreen = (): SleepScreenMetadata => ({
  desired_revision: 'none-1',
  state: 'none',
  support: 'supported',
  platform: 'kindle',
  plugin_version: '5.0.0',
  screen_width: 1264,
  screen_height: 1680,
  observed_at: '2026-10-12T12:00:00',
  applied_revision: null,
  applied_status: null,
  applied_at: null,
  last_error: null,
  preview_url: null,
});

async function mockDeviceAndSleepScreenApi(page: Page, locale = 'en') {
  let current: SleepScreenMetadata = emptyScreen();
  let saves = 0;
  let removals = 0;
  let uploadError = false;

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === '/api/v1/auth/me') {
      return route.fulfill({ json: { ...me, locale } });
    }
    if (path === '/api/v1/i18n/fr.json') {
      return route.fulfill({ json: { locale: 'fr', catalog: {
        'Sleep screen': 'Écran de veille',
        'Sleep screen for {name}': 'Écran de veille de {name}',
      } } });
    }
    if (path === '/api/annotations/devices' && method === 'GET') {
      return route.fulfill({ json: { devices: [reader, nativeKobo], limit: 100, offset: 0, total: 2 } });
    }
    if (path === `/api/v1/devices/${reader.public_id}/sleep-screen`) {
      if (method === 'GET') return route.fulfill({ json: current });
      if (method === 'PUT') {
        if (uploadError) {
          uploadError = false;
          return route.fulfill({ status: 413, json: {
            error: { code: 'sleep_screen_upload_too_large', message: 'Image too large' },
          } });
        }
        saves += 1;
        current = {
          ...current,
          desired_revision: `image-${saves}`,
          state: 'waiting_for_device',
          applied_revision: null,
          applied_status: null,
          last_error: null,
          preview_url: `/api/v1/devices/${reader.public_id}/sleep-screen/image?revision=image-${saves}`,
        };
        return route.fulfill({ json: current });
      }
      if (method === 'DELETE') {
        removals += 1;
        current = {
          ...current,
          desired_revision: `remove-${removals}`,
          state: 'remove',
          applied_revision: null,
          applied_status: null,
          preview_url: null,
        };
        return route.fulfill({ json: current });
      }
    }
    if (path.endsWith('/sleep-screen/image')) {
      return route.fulfill({ status: 200, contentType: 'image/png', body: imageFile.buffer });
    }
    if (path.endsWith('/summary')) {
      return route.fulfill({ json: {
        highlights: 0, notes: 0, dogears: 0, books_with_position: 0,
        last_position_at: null, seeded_books: 0, unseeded_books: 0,
      } });
    }
    if (path.endsWith('/positions')) {
      return route.fulfill({ json: { limit: 100, offset: 0, total: 0, positions: [] } });
    }
    if (path.endsWith('/annotations')) {
      return route.fulfill({ json: {
        device: reader, annotations: [], devices: {}, page: 1, pages: 0,
        page_size: 50, total: 0, role: null, type: null,
      } });
    }
    if (path.endsWith('/inventory')) {
      return route.fulfill({ json: { observed_at: null, limit: 200, offset: 0, total: 0, books: [] } });
    }
    if (path.startsWith('/api/')) {
      return route.fulfill({ json: {} });
    }
    return route.continue();
  });

  return {
    savedCount: () => saves,
    removedCount: () => removals,
    failNextUpload: () => { uploadError = true; },
    acknowledgeCurrentImage: () => {
      current = {
        ...current,
        state: 'image',
        applied_revision: current.desired_revision,
        applied_status: 'applied',
        applied_at: '2026-10-12T12:01:00',
      };
    },
  };
}

const imageFile = {
  name: 'sleep-screen.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/Q5sAAAAASUVORK5CYII=',
    'base64',
  ),
};

test.use({ storageState: { cookies: [], origins: [] } });

test('a reader can set, replace, remove, and verify a device sleep screen from its card', async ({ page }) => {
  const api = await mockDeviceAndSleepScreenApi(page);
  await page.goto('/app/account/devices');

  const card = page.getByRole('listitem').filter({ hasText: 'Bedroom Kindle' });
  await expect(card).toBeVisible();
  const nativeCard = page.getByRole('listitem').filter({ hasText: 'Native Kobo' });
  await nativeCard.getByRole('button', { name: 'More actions for Native Kobo' }).click();
  await expect(nativeCard.getByRole('button', { name: 'Sleep screen', exact: true })).toHaveCount(0);
  await nativeCard.getByRole('button', { name: 'More actions for Native Kobo' }).click();
  const menuTrigger = card.getByRole('button', { name: 'More actions for Bedroom Kindle' });
  await menuTrigger.click();
  await expect(card.getByRole('button', { name: 'Sleep screen', exact: true })).toBeVisible();
  await card.getByRole('button', { name: 'Sleep screen', exact: true }).click();

  let editor = page.getByRole('dialog', { name: 'Sleep screen for Bedroom Kindle' });
  await expect(editor).toBeVisible();
  await expect(editor.getByText('Waiting for device details')).toHaveCount(0);
  await expect(editor.getByText('Shown when this device sleeps in KOReader.')).toBeVisible();
  await expect(editor.getByText('The native Kindle lock screen is unchanged.')).toBeVisible();
  await expect(editor.getByRole('img', { name: 'Sleep screen preview for Bedroom Kindle' })).toBeVisible();

  await editor.getByLabel('Choose an image').setInputFiles(imageFile);
  await editor.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(api.savedCount).toBe(1);
  await expect(editor.getByText('Waiting for next sync')).toBeVisible();

  api.failNextUpload();
  await editor.getByLabel('Choose an image').setInputFiles({ ...imageFile, name: 'rejected.png' });
  await editor.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(editor.getByText('Could not save sleep screen. Try again.')).toBeVisible();
  await expect.poll(api.savedCount).toBe(1);

  api.acknowledgeCurrentImage();
  await page.reload();
  await menuTrigger.click();
  await card.getByRole('button', { name: 'Sleep screen', exact: true }).click();
  editor = page.getByRole('dialog', { name: 'Sleep screen for Bedroom Kindle' });
  await expect(editor.getByText('Applied on device')).toBeVisible();

  await editor.getByLabel('Choose an image').setInputFiles({ ...imageFile, name: 'replacement.png' });
  await editor.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(api.savedCount).toBe(2);
  await expect(editor.getByText('Waiting for next sync')).toBeVisible();

  page.on('dialog', (dialog) => void dialog.accept());
  await editor.getByRole('button', { name: 'Remove sleep screen' }).click();
  const confirmation = page.getByRole('alertdialog');
  if (await confirmation.count()) {
    await expect(confirmation).toContainText(
      'Your previous KOReader sleep-screen settings will return after the next sync.',
    );
    await confirmation.getByRole('button', { name: /remove/i }).click();
  }
  await expect.poll(api.removedCount).toBe(1);
  await expect(editor.getByText('Removal waiting for sync')).toBeVisible();

  const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(scan.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? ''))).toEqual([]);
});

test.describe('phone touch flow', () => {
  test.use({ isMobile: true, hasTouch: true, viewport: { width: 375, height: 667 } });

  test('the editor stays within a phone viewport and cancel returns focus to device actions', async ({ page }) => {
    await mockDeviceAndSleepScreenApi(page, 'fr');
    await page.goto('/app/account/devices');

    const card = page.getByRole('listitem').filter({ hasText: 'Bedroom Kindle' });
    const menuTrigger = card.getByRole('button', { name: 'More actions for Bedroom Kindle' });
    await menuTrigger.tap();
    const sleepAction = card.getByRole('button', { name: 'Écran de veille', exact: true });
    await expect(sleepAction).toBeVisible();
    await sleepAction.tap();
    const editor = page.getByRole('dialog', { name: 'Écran de veille de Bedroom Kindle' });
    await expect(editor).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.keyboard.press('Escape');
    await expect(editor).toHaveCount(0);
    await expect(menuTrigger).toBeFocused();
  });
});
