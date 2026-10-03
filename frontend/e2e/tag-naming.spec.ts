import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow, assertNoPageErrors, collectPageErrors } from './utils';

test('Statistics and tag browsing use the same Calibre field name', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app/about');
  const main = page.getByRole('main');
  await expect(main.getByRole('heading', { name: 'Statistics', exact: true })).toBeVisible();
  await expect(main.getByText('Tags', { exact: true })).toBeVisible();
  await expect(main.getByText('Categories', { exact: true })).toHaveCount(0);
  await assertNoHorizontalOverflow(page);
  const audit = await new AxeBuilder({ page }).include('main')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(audit.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? ''))
    .map((v) => v.id)).toEqual([]);

  const drawer = page.getByRole('button', { name: /open navigation/i });
  if (await drawer.isVisible()) {
    await drawer.focus();
    await page.keyboard.press('Enter');
  }
  const tags = page.getByRole('navigation').getByRole('link', { name: 'Tags', exact: true });
  await expect(tags).toBeVisible();
  await tags.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/app\/tags(?:\?|$)/);
  await expect(page.getByRole('main').getByRole('heading', { name: 'Tags', exact: true })).toBeVisible();
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});
