import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Reverse-proxy sub-path — the v4.1.1 (reader CSS 404) and #571 (white page)
 * class. Runs only when E2E_SUBPATH_URL points at the cwn-nginx-571 rig, via the
 * `subpath` project. Asserts the app boots under a base path with assets served
 * (no 404s / white page) and navigation stays inside the prefix.
 */
test('SPA boots and serves assets under a reverse-proxy sub-path', async ({ page }) => {
  const errors = collectPageErrors(page);
  const bad404s: string[] = [];
  page.on('response', (r) => {
    if (r.status() === 404 && /\.(js|css|png|svg|woff2?)(\?|$)/.test(r.url())) bad404s.push(r.url());
  });

  await page.goto('/app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

  // Nav links must carry the base prefix, not resolve to bare root.
  const href = await page.locator('a[href*="/book/"]').first().getAttribute('href');
  expect(href, 'book link lost the sub-path prefix').toContain('/app/book/');

  expect(bad404s, `static assets 404'd under sub-path:\n${bad404s.join('\n')}`).toEqual([]);
  assertNoPageErrors(errors);
});
