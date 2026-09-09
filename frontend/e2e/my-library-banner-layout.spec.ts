import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('library introduction keeps a readable measure across desktop and narrow screens', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch(); const me = await response.json();
    await route.fulfill({ response, json: { ...me, library_mode: 'personal_library', show_my_library_intro: true, role: { ...me.role, browse_global: true } } });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const width of [2048, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/app/account');
    const banner = page.locator('[data-announcement-id="library-intro-v1"]');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Nothing you had is gone.');
    await expect(banner).toContainText('Global Library');
    await page.evaluate(() => document.fonts.ready);
    // Resizing across the sidebar breakpoint can leave transient scroll geometry
    // while the browser completes layout; persistent overflow must still fail.
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    const metrics = await banner.evaluate(element => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const lines: { left: number; right: number; width: number }[] = [];
      while (walker.nextNode()) {
        const node = walker.currentNode; if (!node.textContent?.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(node);
        for (const rect of range.getClientRects()) lines.push({ left: rect.left, right: rect.right, width: rect.width });
      }
      return { lines, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
    });
    expect(metrics.overflow).toBe(false); expect(metrics.lines.length).toBeGreaterThan(0);
    for (const line of metrics.lines) {
      expect(line.width, 'Long notice text needs a readable line length, even on a wide display').toBeLessThanOrEqual(960);
      expect(line.left).toBeGreaterThanOrEqual(0); expect(line.right).toBeLessThanOrEqual(width);
    }
    const result = await new AxeBuilder({ page }).include('[data-announcement-id="library-intro-v1"]').withTags(['wcag2a','wcag2aa','wcag22aa']).analyze();
    expect(result.violations.filter(v => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
    await expect(banner.getByRole('button', { name: 'Dismiss library introduction' })).toBeEnabled();
  }
});


test('short help notice keeps dismissal hit area inside its strip', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch(); const me = await response.json();
    await route.fulfill({ response, json: { ...me, show_my_library_intro: false } });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/app/account');
  await page.evaluate(() => {
    localStorage.removeItem('cwng_help_banner_dismissed_v1');
    localStorage.removeItem('cwng_banner_dismissed:help-announcement-v1');
  });
  await page.reload();
  const banner = page.locator('[data-announcement-id="help-announcement-v1"]');
  await expect(banner).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const hits = await banner.evaluate(element => {
    const button = element.querySelector('button')!;
    const bounds = element.getBoundingClientRect();
    const close = button.getBoundingClientRect();
    const x = close.left + close.width / 2;
    return {
      center: button.contains(document.elementFromPoint(x, close.top + close.height / 2)),
      below: button.contains(document.elementFromPoint(x, bounds.bottom + 2)),
    };
  });
  expect(hits.center).toBe(true);
  expect(hits.below, 'Dismissal must not steal clicks from page content below the notice').toBe(false);
});
