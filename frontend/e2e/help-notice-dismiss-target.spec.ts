import { test, expect } from '@playwright/test';

// The dismiss button's enlarged target is taller than a one-line notice strip.
// It must stay inside the strip, or a click on the page just below the notice
// dismisses it instead (measured 2.5px of overlap at 1280 and 1440px).
test('one-line help notice keeps its dismiss target inside the strip', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    await route.fulfill({ response, json: { ...me, show_my_library_intro: false } });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const [width, height] of [[1440, 900], [1280, 800]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/app/account');
    await page.evaluate(() => {
      localStorage.removeItem('cwng_help_banner_dismissed_v1');
      localStorage.removeItem('cwng_banner_dismissed:help-announcement-v1');
    });
    await page.reload();
    const banner = page.locator('[data-announcement-id="help-announcement-v1"]');
    await expect(banner).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const hits = await banner.evaluate((element) => {
      const button = element.querySelector('button')!;
      const strip = element.getBoundingClientRect();
      const close = button.getBoundingClientRect();
      const x = close.left + close.width / 2;
      const owns = (y: number) => button.contains(document.elementFromPoint(x, y));
      return {
        center: owns(close.top + close.height / 2),
        insideBottomEdge: owns(strip.bottom - 3),
        below: [0.5, 1, 2, 3].filter((offset) => owns(strip.bottom + offset)),
      };
    });
    expect(hits.center, `dismiss target is hit at its centre at ${width}px`).toBe(true);
    expect(hits.insideBottomEdge, `the enlarged target still covers the strip at ${width}px`).toBe(true);
    expect(hits.below, `dismissal must not take clicks below the notice at ${width}px`).toEqual([]);
  }
});
