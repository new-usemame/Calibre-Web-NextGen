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
    // Hover expansion is an out-of-flow overlay: the rail's flow box must keep
    // its collapsed footprint (the structural successor of the old -156px
    // margin trick).
    await expect.poll(() => nav.locator('..').evaluate((element) => getComputedStyle(element).width))
      .toBe('64px');

    const pin = page.getByRole('button', { name: 'Pin sidebar' });
    await expect(pin).toHaveAttribute('aria-pressed', 'false');
    await pin.click();

    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('220px');
    // Pinning is the one deliberate flow change: the rail reserves the
    // expanded width so <main> starts past it.
    await expect.poll(() => nav.locator('..').evaluate((element) => getComputedStyle(element).width))
      .toBe('220px');
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
    await expect(page.getByRole('button', { name: 'Pin sidebar' }))
      .toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main');
    await expect(page.locator('[aria-live="polite"]')).toHaveText('Sidebar unpinned.');
    expect(await page.evaluate((key) => localStorage.getItem(key), PIN_STORAGE_KEY)).toBe('0');

    // Browser pointer sampling can skip the gap created by the 220px -> 64px
    // collapse. The first pointermove may therefore already be inside the rail;
    // that single move must restore hover expansion without an extra journey.
    await page.mouse.move(32, 100);
    await expect.poll(() => railWidth(page)).toBe('220px');

    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('64px');
    await nav.hover({ position: { x: 32, y: 80 } });
    await expect.poll(() => railWidth(page)).toBe('220px');
    await page.mouse.move(1000, 300);
    await expect.poll(() => railWidth(page)).toBe('64px');
  });

  test('pinned rail survives a mobile drawer transition back to desktop', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'fine-pointer breakpoint transition only');

    await page.addInitScript((key) => localStorage.setItem(key, '1'), PIN_STORAGE_KEY);
    await page.goto('/app/');

    const nav = page.getByRole('navigation', { name: 'Browse' });
    await expect(page.getByRole('button', { name: 'Unpin sidebar' }))
      .toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => railWidth(page)).toBe('220px');

    await page.setViewportSize({ width: 375, height: 667 });
    await expect(page.getByRole('button', { name: /(?:un)?pin sidebar/i })).toHaveCount(0);
    await expect(nav).toHaveAttribute('inert', '');
    await expect(nav).not.toBeInViewport();

    await page.getByRole('button', { name: 'Open navigation' }).click();
    await expect(nav).not.toHaveAttribute('inert', '');
    await expect(nav).toBeInViewport();
    await expect.poll(() => railWidth(page)).toBe('240px');

    // Keep the mobile drawer open while crossing the breakpoint: `.navOpen`
    // must become the pinned desktop rail, not retain drawer-only behavior.
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.getByRole('button', { name: 'Unpin sidebar' }))
      .toHaveAttribute('aria-pressed', 'true');
    await expect(nav).not.toHaveAttribute('inert', '');
    await expect.poll(() => railWidth(page)).toBe('220px');
    await expect.poll(() => nav.locator('..').evaluate((element) => getComputedStyle(element).width))
      .toBe('220px');
  });

  test('a pinned short rail stays at the top and scrolls to its final item', async ({ page }, testInfo) => {
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
    const pin = page.getByRole('button', { name: 'Pin sidebar' });

    // A Playwright click may scroll its target into view before dispatching the
    // event. The first pin test covers that user-level path. Invoke the native
    // control here so this assertion isolates the product's pin transition: a
    // rerender must not move a rail that was already at its defined top.
    await nav.evaluate((element) => { element.scrollTop = 0; });

    const metricsBefore = await nav.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    }));
    expect(metricsBefore.scrollHeight).toBeGreaterThan(metricsBefore.clientHeight);
    expect(metricsBefore.scrollTop).toBe(0);

    await pin.evaluate((element) => {
      if (!(element instanceof HTMLElement)) {
        throw new Error('Pin sidebar control must be an HTML element');
      }
      element.click();
    });
    await expect(pin).toHaveAttribute('aria-pressed', 'true');
    expect(
      await nav.evaluate((element) => element.scrollTop),
      'pinning must not auto-scroll the rail',
    ).toBe(0);

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
    await expect(nav).not.toBeInViewport();

    await page.getByRole('button', { name: 'Open navigation' }).click();
    await expect(nav).toBeInViewport();
    await expect(page.getByRole('button', { name: /(?:un)?pin sidebar/i })).toHaveCount(0);

    await page.getByRole('button', { name: 'Close menu' }).click();
    await expect(nav).not.toBeInViewport();
    expect(await page.evaluate((key) => localStorage.getItem(key), PIN_STORAGE_KEY)).toBe('1');
  });
});
