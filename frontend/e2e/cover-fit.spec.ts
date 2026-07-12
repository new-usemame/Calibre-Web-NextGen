import { test, expect } from '@playwright/test';

/*
 * #660 — browse-card covers were cropped. The shared BookCover renders its
 * image inside a fixed 2:3 frame (aspect-ratio:2/3; overflow:hidden; a themed
 * --surface-2 matte already present). With `object-fit: cover` any cover whose
 * intrinsic ratio isn't 2:3 has its art scaled up until the frame fills, so the
 * edges are clipped away — exactly the "cuts off a lot of the cover art" the
 * reporter saw. The fix switches the rendered image to `object-fit: contain`,
 * letterboxing the whole cover onto the existing matte WITHOUT changing the
 * card frame (grid density and column count are untouched).
 *
 * Behavioral guard: the rendered cover image computes object-fit:contain. This
 * is RED on the pre-fix build (`cover`) and GREEN after. It runs under both the
 * desktop and mobile matrix projects, so density is exercised at both viewports.
 * The whole-art-is-visible + matte-reads-cleanly assertions are the boss's
 * visual pass (Luna/Playwright screenshots); this pins the mechanism in CI.
 */
test('browse-card covers letterbox (object-fit:contain), not crop (#660)', async ({ page }) => {
  await page.goto('/app');
  const cover = page.locator('a[href*="/book/"] img').first();
  await expect(cover).toBeVisible();
  const objectFit = await cover.evaluate((el) => getComputedStyle(el).objectFit);
  expect(objectFit).toBe('contain');
});
