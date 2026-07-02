import { test, expect } from '@playwright/test';

/*
 * Mobile drawer — the #576 surface (transparent drawer + scroll-through +
 * lower nav items unreachable). Runs only under the mobile project.
 */
test.describe('mobile drawer', () => {
  test('drawer opens, lower nav items are reachable, page is scroll-locked behind it', async ({ page }) => {
    await page.goto('/app');
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

    // The hamburger is only present on mobile.
    const menu = page.getByRole('button', { name: /open navigation/i });
    await expect(menu).toBeVisible();
    await menu.click();

    // Drawer nav links render and the LAST one is *reachable* — the #576 bug was
    // that lower items couldn't be scrolled to inside the drawer. Reachable means
    // scrolling within the drawer brings it into view and it stays clickable.
    const navLinks = page.getByRole('navigation').getByRole('link');
    await expect(navLinks.first()).toBeVisible();
    const last = navLinks.last();
    await last.scrollIntoViewIfNeeded();
    await expect(last).toBeInViewport();

    // While the drawer is open the body must not scroll behind it (#576 fix).
    const bodyOverflow = await page.evaluate(() => getComputedStyle(document.body).overflow);
    expect(bodyOverflow).toMatch(/hidden|clip/);
  });
});

/*
 * View-settings gear menu — the #628 surface. At 360px the wrapping toolbar
 * lands the gear at the LEFT edge; the right-aligned 220px menu anchored to
 * the 38px button then opened at x ≈ -158, offscreen. 360 (not the project's
 * 375) is load-bearing: at 375 the gear happens to wrap to the right and the
 * bug doesn't fire.
 */
test.describe('library gear menu at 360px', () => {
  test.use({ viewport: { width: 360, height: 740 } });

  test('view-settings menu stays fully onscreen when the gear wraps left', async ({ page }) => {
    await page.goto('/app/');
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

    const gear = page.getByRole('button', { name: /view settings/i });
    await expect(gear).toBeVisible();
    await gear.click();

    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    const box = (await menu.boundingBox())!;
    expect(box.x, 'menu extends past the left viewport edge (#628)').toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, 'menu extends past the right viewport edge').toBeLessThanOrEqual(360);

    // The menu is still functional where it lands: the Discover toggle is clickable.
    await expect(menu.getByRole('checkbox')).toBeVisible();
  });
});
