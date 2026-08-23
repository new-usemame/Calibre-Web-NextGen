import { test, expect, type Page } from '@playwright/test';

async function openBookFromAuthor(page: Page): Promise<{ originUrl: string }> {
  await page.goto('/app/authors');
  const author = page.locator('a[href*="/authors/"]').first();
  await expect(author).toBeVisible({ timeout: 20_000 });
  await author.click();
  const originUrl = page.url();

  const book = page.locator('main a[href*="/book/"]').first();
  await expect(book).toBeVisible({ timeout: 20_000 });
  await book.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);
  return { originUrl };
}

async function expandedGeometry(page: Page) {
  const nav = page.getByRole('navigation', { name: 'Browse' });
  const firstControl = page.getByRole('link', { name: '← Back', exact: true });
  await expect.poll(async () => (await nav.boundingBox())?.width ?? 0).toBeGreaterThan(200);
  const [navBox, controlBox] = await Promise.all([nav.boundingBox(), firstControl.boundingBox()]);
  expect(navBox).not.toBeNull();
  expect(controlBox).not.toBeNull();
  return { navRight: navBox!.x + navBox!.width, controlLeft: controlBox!.x };
}

test.describe('desktop sidebar expansion does not cover page controls', () => {
  test.skip(({ isMobile }) => isMobile, 'fine-pointer desktop behavior only');

  test('mouse can leave the expanded rail and click the first page control', async ({ page }) => {
    const { originUrl } = await openBookFromAuthor(page);
    const nav = page.getByRole('navigation', { name: 'Browse' });
    const library = nav.getByRole('link', { name: 'Library', exact: true });
    const firstControl = page.getByRole('link', { name: '← Back', exact: true });

    await library.hover();
    const geometry = await expandedGeometry(page);
    expect(geometry.navRight, 'expanded sidebar must end before page controls begin')
      .toBeLessThanOrEqual(geometry.controlLeft);

    await firstControl.click();
    await expect(page).toHaveURL(originUrl);
  });

  test('keyboard focus expansion leaves the first page control unobscured', async ({ page }) => {
    const { originUrl } = await openBookFromAuthor(page);
    const nav = page.getByRole('navigation', { name: 'Browse' });
    const library = nav.getByRole('link', { name: 'Library', exact: true });
    const firstControl = page.getByRole('link', { name: '← Back', exact: true });

    await page.mouse.move(1000, 700);
    await library.focus();
    await expect(library).toBeFocused();
    const geometry = await expandedGeometry(page);
    expect(geometry.navRight, 'focus-expanded sidebar must end before page controls begin')
      .toBeLessThanOrEqual(geometry.controlLeft);

    await firstControl.focus();
    await expect(firstControl).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(originUrl);
  });
});
