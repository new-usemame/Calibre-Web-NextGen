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
 * by this author), the detail-page cover, and the table/duplicates thumbnails.
 *
 * Behavioral guard: the rendered frame computes a 1px border whose colour is
 * exactly the theme's --border. RED on the pre-fix build (border-width 0px),
 * GREEN after. Asserting against the resolved token — rather than a literal
 * colour — keeps it honest on every theme and stops a hard-coded grey from
 * passing; pinning the exact 1px (rather than "non-zero") is what catches the
 * doubled 2px edge that stacking a second border inside the frame would cause.
 * Runs under both the desktop and mobile matrix projects.
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
  expect(width).toBe('1px');
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
  expect(width).toBe('1px');
  expect(color).toBe(tokenRgb);
});

/*
 * The changelog claims "every cover surface", so the row thumbnails are part of
 * the contract too, not just the two big surfaces above. Both are seed-resilient:
 * they skip rather than fail when the view or the seed has no cover thumbnail.
 */
test('table-view row thumbnails carry the same hairline (#987)', async ({ page }) => {
  // The table's 56px cover column is a desktop-width affordance — at 375px the
  // thumbnail is present in the DOM but not presented, so the mobile project
  // would fail on visibility rather than on the border. Assert at a width where
  // the surface actually exists; the grid and detail cases above are the ones
  // carrying the mobile-viewport coverage.
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/app/table');
  // Rows arrive from a client-side fetch — wait for one rather than counting
  // immediately, which would skip the assertion before the data lands.
  const thumb = page.locator('img[class*="coverThumb"]').first();
  await expect(thumb).toBeVisible();
  const { width, color, tokenRgb } = await borderOf(page, 'img[class*="coverThumb"]');
  expect(width).toBe('1px');
  expect(color).toBe(tokenRgb);
});

test('duplicate-list row thumbnails carry the same hairline (#987)', async ({ page }) => {
  await page.goto('/app/duplicates');
  // The duplicates report is genuinely empty on most seeds, so this one stays
  // conditional — but give the client-side fetch time to land before deciding.
  const thumb = page.locator('img[class*="cover"]').first();
  const appeared = await thumb.waitFor({ state: 'visible', timeout: 10_000 }).then(() => true, () => false);
  test.skip(!appeared, 'no duplicate rows in this seed');
  const { width, color, tokenRgb } = await borderOf(page, 'img[class*="cover"]');
  expect(width).toBe('1px');
  expect(color).toBe(tokenRgb);
});
