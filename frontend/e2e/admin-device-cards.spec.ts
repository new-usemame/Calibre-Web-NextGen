import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type TestInfo } from '@playwright/test';

const devices = [
  {
    public_id: 'device-1', label: 'Libra Colour', type: 'kobo', kind: 'kobo',
    kind_label: 'Kobo', model: 'Kobo Libra Colour', firmware: '4.45.23684',
    first_seen: '2026-08-01T12:00:00', last_seen: '2026-08-29T12:00:00',
    annotation_count: 8, highlights: 5, notes: 2, dogears: 1,
    inventory_count: 18, inventory_observed: '2026-08-29T12:00:00',
    storage_free: 1024, storage_total: 2048, storage_observed: '2026-08-29T12:00:00',
    seeded_books: 11, unseeded_books: 7,
    authority: {
      unseeded: 2, seeding: 1, authoritative: 10, quarantined: 0, disabled: 0,
      books_partially_seeded: 1,
    },
    active: true, user: { id: 7, name: 'Household reader' },
  },
  {
    public_id: 'device-2', label: 'Clara BW', type: 'kobo', kind: 'kobo',
    kind_label: 'Kobo', model: 'Kobo Clara BW', firmware: '4.41.23145',
    first_seen: '2026-07-01T12:00:00', last_seen: '2026-08-20T12:00:00',
    annotation_count: 0, highlights: 0, notes: 0, dogears: 0,
    inventory_count: 9, inventory_observed: '2026-08-20T12:00:00',
    storage_free: null, storage_total: null, storage_observed: null,
    seeded_books: 9, unseeded_books: 0,
    authority: {
      unseeded: 0, seeding: 0, authoritative: 9, quarantined: 0, disabled: 0,
      books_partially_seeded: 0,
    },
    active: true, user: { id: 7, name: 'Household reader' },
  },
  {
    public_id: 'device-3', label: 'Old browser', type: 'web', kind: 'browser',
    kind_label: 'Browser', model: 'Safari', firmware: null,
    first_seen: '2026-01-01T12:00:00', last_seen: null,
    annotation_count: 1, highlights: 1, notes: 0, dogears: 0,
    inventory_count: 0, inventory_observed: null,
    storage_free: null, storage_total: null, storage_observed: null,
    seeded_books: 0, unseeded_books: 1,
    authority: {
      unseeded: 1, seeding: 0, authoritative: 0, quarantined: 0, disabled: 0,
      books_partially_seeded: 0,
    },
    active: false, user: { id: 7, name: 'Household reader' },
  },
];

test.use({ contextOptions: { reducedMotion: 'reduce' } });

async function stubDeviceBoard(page: Page) {
  await page.route('**/api/admin/devices*', (route) => route.fulfill({
    json: { devices, limit: 50, offset: 0, total: devices.length },
  }));
}

async function assertNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((violation) => (
    ['critical', 'serious'].includes(violation.impact || '')
  ))).toEqual([]);
}

async function attachEvidence(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.jpg`);
  await page.screenshot({ path, type: 'jpeg', quality: 70, fullPage: true });
  await testInfo.attach(name, { path, contentType: 'image/jpeg' });
}

test('device cards stay compact until their accessible disclosure is activated', async ({ page }, testInfo) => {
  await stubDeviceBoard(page);
  await page.goto('/app/admin/devices');
  await expect(page.getByRole('heading', { name: 'Device administration' })).toBeVisible();

  const cards = page.getByTestId('admin-device-card');
  await expect(cards).toHaveCount(3);
  const libra = cards.filter({ hasText: 'Libra Colour' });
  const clara = cards.filter({ hasText: 'Clara BW' });
  const libraSummary = libra.getByTestId('admin-device-summary');
  await expect(libraSummary).toContainText('Kobo · Kobo Libra Colour');
  await expect(libraSummary).toContainText('7 highlights and notes');
  await expect(libraSummary).toContainText('18 books in latest inventory');
  await expect(clara.getByTestId('admin-device-summary')).not.toContainText(/\b0\b/);

  const progress = libra.getByRole('progressbar', { name: 'Seeded books' });
  await expect(progress).toHaveAttribute('aria-valuemin', '0');
  await expect(progress).toHaveAttribute('aria-valuemax', '18');
  await expect(progress).toHaveAttribute('aria-valuenow', '11');
  await expect(progress).toHaveAttribute('aria-valuetext', '11 of 18 books seeded');

  await expect(libra).not.toContainText('Partially seeded books');
  const disclosure = libra.locator('button[aria-controls]');
  await expect(disclosure).toHaveAccessibleName('Show device details');
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  await expect(disclosure.locator('xpath=ancestor::a')).toHaveCount(0);
  await expect(libra.getByRole('link')).toHaveCount(0);
  const controlledId = await disclosure.getAttribute('aria-controls');
  expect(controlledId).toBeTruthy();
  const controlledPanel = page.locator(`[id="${controlledId}"]`);
  await expect(controlledPanel).toHaveCount(0);

  const urlBefore = page.url();
  if (testInfo.project.name === 'mobile') await disclosure.tap();
  else {
    await disclosure.focus();
    await disclosure.press('Enter');
  }
  await expect(page).toHaveURL(urlBefore);
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  await expect(disclosure).toHaveAccessibleName('Hide device details');
  await expect(controlledPanel).toBeVisible();
  await expect(libra).toContainText('Partially seeded books');
  for (const label of [
    'Kind', 'Model', 'Highlights', 'Notes', 'Dog-ears', 'Seeded books',
    'Unseeded books', 'Authoritative books', 'Books seeding',
    'Books awaiting authority', 'Quarantined books', 'Disabled books',
    'Partially seeded books',
  ]) {
    await expect(controlledPanel.getByText(label, { exact: true })).toBeVisible();
  }

  for (const theme of ['light', 'dark'] as const) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await assertNoSeriousAxeViolations(page);
  }

  await disclosure.press('Space');
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  await expect(libra).not.toContainText('Partially seeded books');

  if (testInfo.project.name === 'desktop') {
    const boxes = await cards.evaluateAll((nodes) => nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return { x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height) };
    }));
    expect(new Set(boxes.map((box) => box.y)).size).toBe(1);
    expect(new Set(boxes.map((box) => box.x)).size).toBe(3);
    expect(Math.max(...boxes.map((box) => box.height))).toBeLessThan(300);
  }

  for (const theme of ['light', 'dark'] as const) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await assertNoSeriousAxeViolations(page);
    await attachEvidence(page, testInfo, `after-${testInfo.project.name}-${theme}`);
  }
});
