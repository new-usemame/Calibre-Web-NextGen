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
