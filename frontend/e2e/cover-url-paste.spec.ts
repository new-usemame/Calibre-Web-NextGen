import { test, expect } from '@playwright/test';

/*
 * Cover picker — "Paste URL" panel.
 *
 * Two things the panel must surface rather than swallow:
 *
 *  1. When the server validated a Google Images results link, it answers with
 *     `resolved_url` (the image behind the link). The button must enable and
 *     the panel must say which image will be used — the typed text still
 *     matches `url`, so the stale-response guard keeps working.
 *  2. When the validation call itself fails (non-2xx, network), the reason
 *     must show in the red feedback line. Before this, the catch branch reset
 *     state to nothing and the user was left with a silently disabled button.
 *
 * The preview endpoint is mocked; nothing here depends on an image host.
 */

const IMGRES = 'https://www.google.com/imgres?imgurl=https%3A%2F%2Fexample.test%2Fx.jpg&imgrefurl=https%3A%2F%2Fexample.test%2Fbook';

async function openUrlPanel(page: import('@playwright/test').Page) {
  await page.goto('/app/');
  const id = await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
  test.skip(!id, 'seed has no books');

  await page.goto(`/app/book/${id}/cover`);
  await page.getByRole('tab', { name: 'Paste URL' }).click();
  return page.getByRole('textbox', { name: 'Cover image URL' });
}

test('a Google Images link validates as the image behind it and can be applied', async ({ page }) => {
  await page.route('**/cover/preview**', async (route) => {
    const typed = (route.request().postDataJSON() as { url: string }).url;
    await route.fulfill({ json: {
      valid: true, url: typed, resolved_url: 'https://example.test/x.jpg',
      error_code: null, error_message: null, content_type: 'image/jpeg',
      size_bytes: 250000, width: 900, height: 1200,
    } });
  });
  const input = await openUrlPanel(page);
  await input.fill(IMGRES);

  await expect(page.getByText('Using the image behind that Google link')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Use this cover' })).toBeEnabled();
  // The preview thumbnail is the image that will be applied, not the results page.
  await expect(page.locator('img[src="https://example.test/x.jpg"]')).toHaveCount(1);
});

test('a failed validation call shows its reason instead of a silently disabled button', async ({ page }) => {
  await page.route('**/cover/preview**', (route) => route.fulfill({
    status: 500, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'internal', message: 'Cover check crashed' } }),
  }));
  const input = await openUrlPanel(page);
  await input.fill('https://example.test/cover.jpg');

  const alert = page.getByRole('alert');
  await expect(alert).toBeVisible();
  await expect(alert).toHaveText('Cover check crashed');
  await expect(page.getByRole('button', { name: 'Use this cover' })).toBeDisabled();
});
