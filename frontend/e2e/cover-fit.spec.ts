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

/*
 * #987 (reported by @chloeroform) — a cover whose own artwork background is the
 * same colour as the page has no edge of its own, so it reads as "swallowed" by
 * the page. Every cover surface now carries a hairline in the themed --border
 * token: the shared BookCover frame (browse grid, catalog, Discover strip, More
 * by this author) and the detail-page cover.
 *
 * Behavioral guard: the rendered frame computes a non-zero border whose colour
 * is exactly the theme's --border. RED on the pre-fix build (border-width 0px),
 * GREEN after. Asserting against the resolved token — rather than a literal
 * colour — keeps it honest on every theme and stops a hard-coded grey from
 * passing. Runs under both the desktop and mobile matrix projects.
 */
async function borderOf(page: import('@playwright/test').Page, selector: string) {
  return page.locator(selector).first().evaluate((el) => {
    const cs = getComputedStyle(el);
    const token = getComputedStyle(document.documentElement)
      .getPropertyValue('--border')
      .trim();
    // Resolve the token through the browser so both sides are the same format.
    const probe = document.createElement('span');
    probe.style.color = token;
    document.body.appendChild(probe);
    const tokenRgb = getComputedStyle(probe).color;
    probe.remove();
    return { width: cs.borderTopWidth, color: cs.borderTopColor, tokenRgb };
  });
}

test('browse-card covers carry a themed hairline, not a swallowed edge (#987)', async ({ page }) => {
  await page.goto('/app');
  const frame = 'a[href*="/book/"] img';
  await expect(page.locator(frame).first()).toBeVisible();

  // The hairline lives on the BookCover frame that wraps the <img>.
  const { width, color, tokenRgb } = await borderOf(page, 'a[href*="/book/"] img >> xpath=..');
  expect(width).not.toBe('0px');
  expect(color).toBe(tokenRgb);
});

test('detail-page cover carries a themed hairline (#987)', async ({ page }) => {
  await page.goto('/app');
  const link = page.locator('a[href*="/book/"]').first();
  await expect(link).toBeVisible();
  const href = await link.getAttribute('href');
  test.skip(!href, 'seed has no books to open');
  await page.goto(href!);

  // CSS-module hashed class — `_cover_ab12` — identifies the detail cover
  // itself, not the "More by this author" strip further down the page.
  const cover = page.locator('img[class*="cover"]').first();
  await expect(cover).toBeVisible();
  const { width, color, tokenRgb } = await borderOf(page, 'img[class*="cover"]');
  expect(width).not.toBe('0px');
  expect(color).toBe(tokenRgb);
});
