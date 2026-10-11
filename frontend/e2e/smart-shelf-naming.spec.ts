import { test, expect } from './fixtures';
import { assertNoHorizontalOverflow, assertNoPageErrors, collectPageErrors } from './utils';

test('Classic creation uses the same smart-shelf name as New UI', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app/magic');
  await expect(page.getByRole('main').getByRole('heading', { name: 'Smart shelves', exact: true })).toBeVisible();
  await page.goto('/magicshelf');
  await expect(page.getByRole('heading', { name: 'Create smart shelf', exact: true })).toBeVisible();
  await expect(page.locator('#nav_createmagicshelf')).toHaveText('Create smart shelf');
  const field = page.getByRole('combobox', { name: 'Rule field', exact: true });
  await expect(field).toBeVisible();
  await field.selectOption('title');
  await expect(page.getByRole('combobox', { name: 'Rule operator', exact: true })).toBeVisible();
  await page.locator('#shelf-name').fill('Keyboard-only icon selection');
  await page.getByRole('combobox', { name: 'Rule operator', exact: true }).selectOption('contains');
  const value = page.getByRole('textbox', { name: 'Title: value', exact: true });
  await expect(value).toBeVisible();
  await field.selectOption('author');
  await expect(page.getByRole('textbox', { name: 'Author: value', exact: true })).toBeVisible();
  await field.selectOption('title');
  await page.getByRole('combobox', { name: 'Rule operator', exact: true }).selectOption('contains');
  await value.fill('Native');
  const initialIcon = await page.locator('#shelf-icon').inputValue();
  const icons = page.locator('#icon-picker-grid button');
  const differentIcon = await icons.evaluateAll((buttons, initial) =>
    buttons.findIndex(button => button.getAttribute('data-icon') !== initial), initialIcon);
  expect(differentIcon, 'keyboard selection must change the initial icon').toBeGreaterThanOrEqual(0);
  const icon = icons.nth(differentIcon);
  await expect(icon).toBeVisible();
  const chosenIcon = await icon.getAttribute('data-icon');
  const form = page.locator('#magic-shelf-form');
  await form.evaluate(element => {
    element.setAttribute('data-keyboard-submits', '0');
    element.addEventListener('submit', () =>
      element.setAttribute('data-keyboard-submits', String(Number(element.getAttribute('data-keyboard-submits')) + 1)));
  });
  expect(await form.evaluate(element => (element as HTMLFormElement).checkValidity()),
    'a valid form makes unintended submission observable').toBe(true);
  await icon.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#shelf-icon')).toHaveValue(chosenIcon!);
  await expect(form).toHaveAttribute('data-keyboard-submits', '0');
  await expect(page).toHaveURL(/\/magicshelf$/);
  await expect(page.getByText('What are smart shelves?', { exact: true })).toBeVisible();
  await page.getByText('What are smart shelves?', { exact: true }).click();
  await expect(page.getByText('Smart shelves were called Magic Shelves in older versions.', { exact: true })).toBeVisible();
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});

test('stored smart-shelf display values are text in Classic headings, profile and activity', async ({ page, secondaryUser }, testInfo) => {
  // #1498: renaming the existing safe-HTML heading revealed stored markup injection.
  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.status()).toBe(200);
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const payload = '<img src="/not-a-shelf-image" onerror="window.__shelfProbe=1">';
  for (const field of ['name', 'icon'] as const) {
    const data = {
      name: `Heading ${testInfo.project.name} ${field} ${Date.now()}`,
      icon: '🪄',
      rules: { condition: 'AND', rules: [{ id: 'title', field: 'title', type: 'string', operator: 'contains', value: 'Native' }] },
      is_public: true,
    };
    data[field] = payload;
    const created = await page.request.post('/magicshelf', { headers, data });
    expect(created.status()).toBe(200);
    const result = await created.json() as { success: boolean; shelf_id: number };
    expect(result.success).toBe(true);
    try {
      await page.addInitScript(() => { (window as unknown as Record<string, unknown>).__shelfProbe = 0; });
      await page.goto(`/magicshelf/${result.shelf_id}`);
      const heading = page.locator('.discover.load-more h2');
      await expect(heading.locator('img')).toHaveCount(0);
      await expect(heading).toContainText(payload);
      await expect(heading).toContainText('Smart shelf —');
      expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__shelfProbe)).toBe(0);
      await page.goto('/me');
      const row = page.locator(`#magic_shelf_order_list li[data-key="${result.shelf_id}"]`);
      await expect(row).toHaveCount(1);
      await expect(row.locator('img')).toHaveCount(0);
      await expect(row.locator(field === 'name' ? '.opds-order-label' : '.magic-shelf-icon')).toHaveText(payload);
      expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__shelfProbe)).toBe(0);
      if (field === 'name') {
        // A fresh viewer records this real public-shelf view. Filter by that
        // user so earlier suite activity cannot push it out of the top ten.
        const viewed = await secondaryUser.page.request.get(`/magicshelf/${result.shelf_id}`);
        expect(viewed.status()).toBe(200);
        await page.goto(`/cwa-stats-show?tab=activity&user_id=${secondaryUser.id}`);
        const activity = page.locator('#shelf-activity-list');
        await expect(activity).toBeVisible();
        await expect(activity.locator('img')).toHaveCount(0);
        await expect(activity).toContainText(payload);
        expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__shelfProbe)).toBe(0);
      }
    } finally {
      const removed = await page.request.post(`/magicshelf/${result.shelf_id}/delete`, { headers });
      expect(removed.status(), 'owned test shelf must be removed').toBe(200);
      expect((await removed.json()).success).toBe(true);
    }
  }
});
