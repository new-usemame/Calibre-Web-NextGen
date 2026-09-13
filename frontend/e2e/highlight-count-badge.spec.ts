import { test, expect } from '@playwright/test';

/*
 * #1393 — the annotations entry on the book page carries the saved-annotation
 * count in its accessible name (and as a trailing badge) without a second
 * request: the count rides in the book detail payload.
 *
 * The link is the gear menu's "View highlights" item: zero state is the plain
 * name with no badge; a counted state appends ", N saved annotations" to the
 * accessible name and renders the badge (data-testid="highlight-count").
 */

test('View highlights keeps its zero state and exposes a counted accessible name (#1393)', async ({ page }) => {
  let annotationCount = 0;
  const annotationsPageRequests: string[] = [];

  page.on('request', (request) => {
    if (/\/annotations\/\d+\/data\.json/.test(request.url())) {
      annotationsPageRequests.push(request.url());
    }
  });
  await page.route(/\/api\/v1\/books\/\d+(?:\?.*)?$/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, annotation_count: annotationCount } });
  });

  await page.goto('/app');
  const href = await page.locator('a[href*="/book/"]').first().getAttribute('href');
  const id = href?.match(/\/book\/(\d+)/)?.[1];
  test.skip(!id, 'seed has no books');

  await page.goto(`/app/book/${id}`);
  await page.getByTestId('book-actions-menu').click();
  const highlights = page.locator(`a[href$="/book/${id}/annotations"]`);
  await expect(highlights).toBeVisible();
  await expect(highlights).toHaveRole('menuitem');
  await expect(highlights).toHaveAccessibleName('View highlights');
  await expect(highlights.locator('[data-testid="highlight-count"]')).toHaveCount(0);
  expect(annotationsPageRequests, 'book detail must not fetch annotations separately').toEqual([]);

  annotationCount = 3;
  await page.reload();
  await page.getByTestId('book-actions-menu').click();
  await expect(highlights.locator('[data-testid="highlight-count"]')).toHaveText('3');
  await expect(highlights).toHaveAccessibleName('View highlights, 3 saved annotations');
  expect(annotationsPageRequests, 'count must ride in the book detail response').toEqual([]);
});
