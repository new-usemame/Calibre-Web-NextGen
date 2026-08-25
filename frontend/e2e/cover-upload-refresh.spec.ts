import { test, expect } from '@playwright/test';

/*
 * #989 — replacing a cover looked like it did nothing.
 *
 * Reported by @chloeroform, who linked the exact call sites. The cover lived
 * at a stable path, so after a replacement the refetched book handed back a
 * byte-identical `src`. React re-renders, the browser serves its cached copy,
 * and the preview never changes — the upload succeeded and looked like a no-op.
 *
 * The API already answers the upload with a cache-busted URL for exactly this
 * reason; the frontend was discarding it.
 *
 * Every cover URL is now versioned by Books.last_modified (`?c=`), which the
 * cover-replace endpoint bumps, so the refetched book carries a different URL
 * too — the buster is no longer only on the upload's own response.
 *
 * Asserts the observable property — the preview's src changes — rather than
 * pixels, because "the image on screen is the new one" is what the reporter
 * was actually missing and a pixel diff would fire on any unrelated restyle.
 */

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC',
  'base64');

test('replacing a cover updates the preview immediately (#989)', async ({ page }) => {
  await page.goto('/app/');
  const id = await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
  test.skip(!id, 'seed has no books');

  await page.goto(`/app/book/${id}/edit`);
  await page.waitForLoadState('networkidle');

  const preview = page.locator('img[alt*="urrent cover"]').first();
  const before = await preview.getAttribute('src').catch(() => null);
  test.skip(!before, 'this book has no cover to replace');

  await page.locator('input[type=file]').first()
    .setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: TINY_PNG });

  await expect
    .poll(() => preview.getAttribute('src'), { timeout: 10_000 })
    .not.toBe(before);

  const after = await preview.getAttribute('src');
  // `?c=<last_modified>` is the server's own cover version token; `?t=` is the
  // legacy per-apply stamp. Either is a real buster — what must never happen is
  // a bare URL, because cover responses are cached hard and the browser would
  // keep showing the old image (#989).
  expect(after, 'the refreshed preview must carry a cache-buster, or the browser '
    + 'serves the old image from cache (#989)').toMatch(/[?&](c|t)=/);
});
