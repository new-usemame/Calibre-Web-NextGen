import { expect, test } from '@playwright/test';

/**
 * Deliberately unstubbed integration probe. cwn-local must run this branch's
 * backend: the test creates through Flask, reads through data.json, and then
 * soft-deletes its own row. It exists specifically because route.fulfill()
 * cannot prove an origin producer is wired.
 */
test('real web-reader create persists a non-null origin device', async ({ page }) => {
  // Ask the library which books exist rather than naming one. A hardcoded id
  // passed against a dev container carrying hundreds of books and 404'd in CI,
  // whose seed is three — the probe was coupled to one machine's data, which is
  // the opposite of what an unstubbed integration test is for.
  const catalog = await page.request.get('/api/v1/books?per_page=1');
  expect(catalog.ok()).toBeTruthy();
  const items = (await catalog.json() as { items?: { id: number }[] }).items ?? [];
  expect(items.length, 'library seed must contain at least one book').toBeGreaterThan(0);
  const bookId = items[0].id;
  const marker = `origin-e2e-${Date.now()}`;
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  expect(csrfResponse.ok()).toBeTruthy();
  const { csrf_token: csrf } = await csrfResponse.json() as { csrf_token: string };
  let annotationId: string | undefined;

  try {
    const created = await page.request.post(`/annotations/${bookId}`, {
      headers: { 'X-CSRFToken': csrf },
      data: {
        cfi_range: 'epubcfi(/6/4!/4/2,/1:0,/1:9)',
        highlighted_text: marker,
        highlight_color: 'yellow',
      },
    });
    expect(created.status()).toBe(201);
    const createdBody = await created.json() as { annotation_id: string };
    annotationId = createdBody.annotation_id;

    const listed = await page.request.get(`/annotations/${bookId}/data.json`);
    expect(listed.ok()).toBeTruthy();
    const payload = await listed.json() as {
      annotations: { annotation_id: string; origin_device_id: string | null }[];
      devices: Record<string, { label: string; type: string }>;
    };
    const row = payload.annotations.find((annotation) => annotation.annotation_id === annotationId);
    expect(row, 'the real data.json must return the row just created').toBeDefined();
    expect(row!.origin_device_id).not.toBeNull();
    expect(payload.devices[row!.origin_device_id!]?.type).toBe('webreader');
  } finally {
    if (annotationId) {
      await page.request.delete(`/annotations/${bookId}/${encodeURIComponent(annotationId)}`, {
        headers: { 'X-CSRFToken': csrf },
      });
    }
  }
});
