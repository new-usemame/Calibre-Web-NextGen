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

// Classic's theme paints a second heading with generated CSS content. DOM-only
// assertions miss a stale visible label even when its accessible heading is right.
test('Classic tag browsing paints the localized field name', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/category');
  await expect(page.getByRole('heading', { name: 'Tags', exact: true })).toBeVisible();
  const list = page.locator('body.catlist > .container-fluid > .row-fluid > .col-sm-10 > .container');
  await expect(list).toBeVisible();
  const painted = await list.evaluate((element) => {
    const style = getComputedStyle(element, '::before');
    return { display: style.display, content: style.content.replace(/^['"]|['"]$/g, '') };
  });
  await expect(page.locator('body')).toHaveClass(/blur/);
  // Require the painted desktop heading so disabling it cannot mask drift.
  if ((page.viewportSize()?.width ?? 1280) > 767) {
    expect(painted.display).not.toBe('none');
    expect(painted.content).toBe('Tags');
  } else {
    // Phones show the real localized h1 checked above.
    expect(painted.display).toBe('none');
  }
  await assertNoHorizontalOverflow(page);
  assertNoPageErrors(errors);
});
