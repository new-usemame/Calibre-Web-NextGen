import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow } from './utils';


async function firstBookId(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', {
      headers: { Accept: 'application/json' },
    });
    return response.ok ? (await response.json())?.items?.[0]?.id ?? null : null;
  });
}


async function stubDeliveryDevice(page: Page, bookId: number) {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.role = { ...(me.role ?? {}), download: true, anonymous: false };
    await route.fulfill({ response, json: me });
  });
  await page.route('**/api/annotations/devices?active=true', (route) =>
    route.fulfill({ json: { devices: [{
      public_id: 'reader-a', label: 'Reader A', type: 'koreader', model: 'KOReader',
      active: true, can_receive_books: true,
    }] } }));
  await page.route(`**/api/v1/books/${bookId}/device-deliveries`, async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON()).toEqual({ device: 'reader-a' });
    await route.fulfill({ json: {
      delivery_id: 17, format: 'EPUB', queued: true, state: 'queued',
      message: 'Book queued for this device',
    } });
  });
}


test('a book can be queued to one selected pull reader', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');
  await stubDeliveryDevice(page, bookId!);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  const disclosure = page.getByRole('button', { name: 'Send to device' });
  await expect(disclosure).toBeVisible();
  await disclosure.click();

  const panel = page.getByTestId('device-send-panel');
  await expect(panel.getByRole('combobox', { name: 'Device' })).toHaveValue('reader-a');
  await expect(panel.getByText("Collects on the device's next sync.")).toBeVisible();
  await panel.getByRole('button', { name: 'Send to device' }).click();
  await expect(panel.getByRole('status')).toHaveText('Book queued for this device');
});


test('send-to-device panel is accessible and does not overflow on touch mobile', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');
  await stubDeliveryDevice(page, bookId!);

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Send to device' }).click();
  await expect(page.getByTestId('device-send-panel')).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((violation) =>
    ['critical', 'serious'].includes(violation.impact || ''))).toEqual([]);
  await assertNoHorizontalOverflow(page);
});
