import { test, expect } from '@playwright/test';

const PIN_STORAGE_KEY = 'cwng:sidebar-pinned';

async function railWidth(page: import('@playwright/test').Page): Promise<string> {
  return page.getByRole('navigation', { name: 'Browse' })
    .evaluate((element) => getComputedStyle(element).width);
}

test.describe('#1839 desktop sidebar pin', () => {
  test('pin persists away from the rail and across reload; unpin restores hover expansion', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');

    await page.goto('/app/');
    await page.evaluate((key) => localStorage.removeItem(key), PIN_STORAGE_KEY);
    await page.reload();

    const nav = page.getByRole('navigation', { name: 'Browse' });
    await expect(nav).toBeVisible();
    await expect.poll(() => railWidth(page)).toBe('64px');

    await nav.hover({ position: { x: 32, y: 80 } });
    await expect.poll(() => railWidth(page)).toBe('220px');
    await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).contain))
      .toBe('layout paint');
    await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).marginRight))
      .toBe('-156px');

    const pin = page.getByRole('button', { name: 'Pin sidebar' });
    await expect(pin).toHaveAttribute('aria-pressed', 'false');
    await pin.click();

    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('220px');
    await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).marginRight))
      .toBe('0px');
    await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).contain))
      .toBe('layout paint');
    await expect.poll(() => page.locator('main#main').evaluate((element) => element.getBoundingClientRect().left))
      .toBeGreaterThanOrEqual(220);

    await page.reload();
    await expect.poll(() => railWidth(page)).toBe('220px');
    await expect(page.getByRole('button', { name: 'Unpin sidebar' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate((key) => localStorage.getItem(key), PIN_STORAGE_KEY)).toBe('1');

    // The pointer stays over the control during the click. Unpinning must still
    // collapse immediately instead of resurrecting the stale-hover regression.
    await page.getByRole('button', { name: 'Unpin sidebar' }).click();
    await expect.poll(() => railWidth(page)).toBe('64px');
    expect(await page.evaluate((key) => localStorage.getItem(key), PIN_STORAGE_KEY)).toBe('0');

    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('64px');
    await nav.hover({ position: { x: 32, y: 80 } });
    await expect.poll(() => railWidth(page)).toBe('220px');
    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('64px');
  });

  test('a pinned short rail with many shelves scrolls to its final item', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');

    await page.setViewportSize({ width: 1280, height: 360 });
    await page.addInitScript((key) => localStorage.setItem(key, '0'), PIN_STORAGE_KEY);
    await page.route('**/api/v1/shelves', (route) => route.fulfill({
      json: {
        items: Array.from({ length: 30 }, (_, index) => ({
          id: 18_390 + index,
          name: `Issue 1839 shelf ${index + 1}`,
          count: index,
          is_public: false,
          is_owner: true,
        })),
      },
    }));

    await page.goto('/app/');
    const nav = page.getByRole('navigation', { name: 'Browse' });
    await nav.hover({ position: { x: 32, y: 80 } });
    await page.getByRole('button', { name: 'Pin sidebar' }).click();

    const metricsBefore = await nav.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    }));
    expect(metricsBefore.scrollHeight).toBeGreaterThan(metricsBefore.clientHeight);
    expect(metricsBefore.scrollTop).toBe(0);

    const finalItem = nav.getByRole('link', { name: 'About', exact: true });
    await finalItem.scrollIntoViewIfNeeded();
    await expect(finalItem).toBeInViewport();
    expect(await nav.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  });

  test('stored pin state is disarmed outside the desktop fine-pointer query', async ({ page }, testInfo) => {
    test.skip(!['mobile', 'ipad-touch'].includes(testInfo.project.name), 'coarse-pointer projects only');

    await page.addInitScript((key) => localStorage.setItem(key, '1'), PIN_STORAGE_KEY);
    await page.goto('/app/');

    await expect(page.getByRole('button', { name: /(?:un)?pin sidebar/i })).toHaveCount(0);
    const nav = page.getByRole('navigation', { name: 'Browse' });
    await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).width)).toBe('240px');
    expect(await page.evaluate((key) => localStorage.getItem(key), PIN_STORAGE_KEY)).toBe('1');
  });
});
