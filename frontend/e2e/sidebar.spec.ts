import { test, expect, Locator } from '@playwright/test';

/*
 * Sidebar shelves grouping — the reported new-UI regression: the user's shelves
 * were listed as the LAST block of the nav (below Tasks / About / Table view /
 * Smart shelves), disconnected from the "SHELVES" section header they belong to.
 * The header said "SHELVES" with the info pages directly under it and the actual
 * shelves pushed below the fold.
 *
 * The contract this pins: within the sidebar, a user's shelf link renders
 *   (a) BELOW the SHELVES section header, and
 *   (b) ABOVE the Tasks / About info pages
 * i.e. the shelves are grouped with their header, not orphaned at the bottom.
 *
 * DOM order in a vertical nav = smaller boundingBox().y. Asserting on y is the
 * user-visible truth (where the shelves actually appear on screen).
 */

async function topY(loc: Locator): Promise<number> {
  const box = await loc.boundingBox();
  if (!box) throw new Error('element has no bounding box (not rendered/visible)');
  return box.y;
}

test.describe('sidebar shelves grouping', () => {
  test('desktop: user shelves render under the SHELVES header, above Tasks/About', async ({ page }) => {
    await page.goto('/app');
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

    const nav = page.locator('nav[aria-label="Browse"]');
    const shelvesHeader = nav.locator('a[href$="/shelves"]');
    const firstShelf = nav.locator('a[href*="/shelf/"]').first();  // /shelf/<id>, not /shelves
    const tasks = nav.locator('a[href$="/tasks"]');

    await expect(shelvesHeader, 'SHELVES section header present').toBeVisible();
    await expect(firstShelf, "at least one of the user's shelves is listed in the sidebar").toBeVisible();
    await expect(tasks, 'Tasks info link present').toBeVisible();

    const headerY = await topY(shelvesHeader);
    const shelfY = await topY(firstShelf);
    const tasksY = await topY(tasks);

    expect(shelfY, 'user shelves must render BELOW the SHELVES header').toBeGreaterThan(headerY);
    expect(shelfY, 'user shelves must render ABOVE the Tasks/About info pages (not orphaned at the bottom)').toBeLessThan(tasksY);
  });

  test('mobile: shelves are grouped under the header inside the opened drawer', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'drawer + hamburger exist only on the mobile project');

    await page.goto('/app');
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
    await page.getByRole('button', { name: /open navigation/i }).click();

    const nav = page.getByRole('navigation');
    const shelvesHeader = nav.locator('a[href$="/shelves"]');
    const firstShelf = nav.locator('a[href*="/shelf/"]').first();
    const tasks = nav.locator('a[href$="/tasks"]');

    await expect(firstShelf).toBeVisible();
    const headerY = await topY(shelvesHeader);
    const shelfY = await topY(firstShelf);
    const tasksY = await topY(tasks);

    expect(shelfY, 'shelves below the SHELVES header in the drawer').toBeGreaterThan(headerY);
    expect(shelfY, 'shelves above Tasks/About in the drawer').toBeLessThan(tasksY);
  });
});
