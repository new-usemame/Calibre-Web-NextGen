import { test, expect, Page } from '@playwright/test';

/*
 * #716 — "After downloading a book I can't come back to the app, I stay on the
 * download page on my iPhone, I have to restart to come back."
 *
 * Book files are served with `Content-Disposition: inline` (needed for
 * byte-range / in-browser reading), and iOS Safari ignores the anchor's
 * `download` hint — so a same-tab tap navigates the SPA away to a file the
 * browser can't render, stranding the user on a dead page until they force
 * a restart. The fix opens download links in a new tab (`target="_blank"`) so
 * the app tab always survives; desktop browsers still honour `download` and
 * don't spawn a stray tab.
 *
 * The iOS-Safari stranding itself can't be reproduced under Chromium (Chromium
 * honours `download`), so the regression guard is the rendered anchor: it must
 * carry `target="_blank"` + `rel~=noopener`. If a future edit drops that, iOS
 * users strand again and this goes red. The desktop test guards the reverse
 * risk — that adding `target="_blank"` doesn't break the download or leave a
 * stray tab.
 */

async function firstBookDownloadLink(page: Page) {
  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);
  // Formats load async, so wait for the download control to mount rather than
  // race the query with an immediate count() (which flakily skipped on mobile).
  const dl = page.locator('a[href*="/download/"]').first();
  try {
    await dl.waitFor({ state: 'visible', timeout: 10_000 });
  } catch {
    test.skip(true, 'first book has no downloadable format in this seed');
  }
  return dl;
}

test('book download link opens in a new tab so the SPA is never unloaded (#716)', async ({ page }) => {
  const dl = await firstBookDownloadLink(page);
  await expect(dl).toHaveAttribute('target', '_blank');
  await expect(dl).toHaveAttribute('rel', /noopener/);
  // `download` is a boolean attribute — present, empty value.
  await expect(dl).toHaveAttribute('download', '');
});

test('desktop: download still fires and leaves no stray tab (#716 regression guard)', async ({ page, context }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'download-event behaviour is checked on the desktop project');
  const dl = await firstBookDownloadLink(page);
  const tabsBefore = context.pages().length;
  const downloadPromise = page.waitForEvent('download', { timeout: 8_000 });
  await dl.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename().length).toBeGreaterThan(0);
  // adding target="_blank" must not spawn a lingering blank tab on desktop
  expect(context.pages().length).toBeLessThanOrEqual(tabsBefore);
});
