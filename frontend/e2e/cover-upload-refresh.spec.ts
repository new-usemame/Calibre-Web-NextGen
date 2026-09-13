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
 * The upload surface moved when the book page was cleaned up: the Edit
 * metadata page no longer carries any cover file input; replacement happens in
 * the cover editor (/book/<id>/cover), Upload tab. The regression guard below
 * lives there, with the provider fan-out route-mocked. The second test pins
 * the edit page's now cover-input-free form.
 *
 * Asserts the observable property — the current cover's src changes — rather
 * than pixels, because "the image on screen is the new one" is what the
 * reporter was actually missing and a pixel diff would fire on any unrelated
 * restyle.
 */

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC',
  'base64');

/** First seed book that has a cover to replace, or null. */
async function firstCoveredBook(page: import('@playwright/test').Page) {
  await page.goto('/app/');
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=25', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    const items: { id: number; title: string; cover_url?: string | null }[] = r?.items ?? [];
    return items.find((b) => !!b.cover_url) ?? null;
  });
}

test('replacing a cover updates the preview immediately (#989)', async ({ page }) => {
  const book = await firstCoveredBook(page);
  test.skip(!book, 'seed has no book with a cover to replace');

  // The cover editor's provider fan-out never leaves the rig; the apply call
  // is stubbed so the seed library is not actually mutated, and answers with
  // the cache-busted URL a real replacement returns.
  await page.route('**/book/*/cover/state', async (route) => {
    await route.fulfill({ json: {
      locked: false,
      ereader_enabled: false,
      ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
    } });
  });
  await page.route('**/book/*/cover/candidates*', async (route) => {
    await route.fulfill({ json: { candidates: [], providers: [], query: '' } });
  });
  const newCoverUrl = `/cover/${book!.id}/og?c=2099-01-01T00:00:00`;
  await page.route('**/book/*/cover/apply', async (route) => {
    await route.fulfill({ json: { ok: true, cover_url: newCoverUrl } });
  });

  await page.goto(`/app/book/${book!.id}/cover`);
  const preview = page.getByRole('img', { name: book!.title, includeHidden: true }).first();
  await expect(preview).toBeAttached({ timeout: 10_000 });
  const before = await preview.getAttribute('src');
  expect(before, 'a covered book shows its current cover in the editor').toBeTruthy();

  await page.getByRole('tab', { name: 'Upload' }).click();
  await page.getByLabel('Choose a cover image to upload')
    .setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: TINY_PNG });
  await page.getByRole('button', { name: 'Upload as cover' }).click();

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
  expect(after).toContain(newCoverUrl);
});

test('Edit metadata carries no cover upload control — replacement lives in the cover editor (#989)', async ({ page }) => {
  await page.goto('/app/');
  const id = await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
  test.skip(!id, 'seed has no books');

  await page.goto(`/app/book/${id}/edit`);
  await expect(page.getByRole('heading', { name: 'Edit metadata' })).toBeVisible();

  // No file input anywhere on the edit page: the cover upload and the format
  // upload both moved out (cover editor and the book page's Files section).
  await expect(page.locator('input[type=file]')).toHaveCount(0);
  // The cover area links out to the editor instead.
  await expect(page.getByTestId('open-cover-editor')).toBeVisible();
  await expect(page.getByTestId('open-cover-editor'))
    .toHaveAttribute('href', new RegExp(`/book/${id}/cover\\?origin=edit$`));
});
