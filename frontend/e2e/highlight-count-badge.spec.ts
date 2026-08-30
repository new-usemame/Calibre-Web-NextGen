import { test, expect } from '@playwright/test';

test('Highlights keeps its zero state and exposes a counted accessible name (#1393)', async ({ page }) => {
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
  const highlights = page.locator(`a[href$="/book/${id}/annotations"]`);
  await expect(highlights).toBeVisible();
  await expect(highlights).toHaveAccessibleName('Highlights');
  await expect(highlights.locator('[data-testid="highlight-count"]')).toHaveCount(0);
  expect(annotationsPageRequests, 'book detail must not fetch annotations separately').toEqual([]);

  annotationCount = 3;
  await page.reload();
  await expect(highlights.locator('[data-testid="highlight-count"]')).toHaveText('3');
  await expect(highlights).toHaveAccessibleName('Highlights, 3 saved annotations');
  expect(annotationsPageRequests, 'count must ride in the book detail response').toEqual([]);
});
