import { test, expect } from '@playwright/test';

/*
 * The cover picker searches every source with the book's title and author,
 * and its toolbar lets the user re-run the sources with their own words —
 * a different title, the original-language one, an ISBN they trust.
 *
 * Route-mocked: no provider traffic. The assertion is about what the SPA
 * sends (no query = the server's default; then the typed words verbatim)
 * and that the response's echoed query becomes the box's placeholder.
 */

const response = (query: string) => ({
  candidates: [{
    source_id: 'openlibrary', source_name: 'Open Library',
    cover_url: `https://example.test/${encodeURIComponent(query)}.jpg`,
    candidate_id: `openlibrary:${query}`, title: query, authors: [],
  }],
  providers: [{ id: 'openlibrary', name: 'Open Library', status: 'ok', count: 1, message: '', duration_ms: 12 }],
  query,
});

test('the picker re-searches the sources with the words the user typed', async ({ page }) => {
  await page.goto('/app/');
  const id = await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
  test.skip(!id, 'seed has no books');

  const sent: string[] = [];
  await page.route('**/cover/candidates*', (route) => {
    const body = route.request().postDataJSON() as { query?: string } | null;
    sent.push(body?.query ?? '');
    return route.fulfill({ json: response(body?.query || 'Default Title Default Author') });
  });

  await page.goto(`/app/book/${id}/cover`);
  const box = page.getByRole('searchbox', { name: 'Search sources with different words' });
  await expect(box).toBeVisible();
  // First search carries no query: the server decides (title + author).
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toBe('');
  await expect(box).toHaveAttribute('placeholder', 'Default Title Default Author');

  await box.fill('Nineteen Eighty-Four Orwell');
  await box.press('Enter');
  await expect.poll(() => sent).toContain('Nineteen Eighty-Four Orwell');
  await expect(box).toHaveAttribute('placeholder', 'Nineteen Eighty-Four Orwell');
});
