import { test, expect } from '@playwright/test';

test('ordinary shelf names remain text in Classic page titles and ordering', async ({ page }, testInfo) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.status()).toBe(200);
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const name = `</title><b data-cwng-display="shelf">Literal ${testInfo.project.name} ${Date.now()}</b>`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name } });
  expect(created.status()).toBe(201);
  const { id } = await created.json() as { id: number };
  try {
    for (const path of [`/shelf/${id}`, `/shelf/order/${id}`]) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.locator('b[data-cwng-display="shelf"]')).toHaveCount(0);
      expect(await page.title()).toContain(name);
    }
  } finally {
    const removed = await page.request.post(`/api/v1/shelves/${id}/delete`, { headers });
    expect(removed.status(), 'owned ordinary shelf must be deleted').toBe(204);
  }
});

test('Classic rule preview displays actual matching book titles as literal text', async ({ page }) => {
  const library = await page.request.get('/api/v1/books?per_page=100');
  expect(library.status()).toBe(200);
  const { items } = await library.json() as { items: Array<{ title: string }> };
  expect(items.length, 'the owning fixture must contain a book').toBeGreaterThan(0);
  // The native adversarial fixture includes markup-looking stored metadata.
  const selected = items.find(book => book.title.includes('<')) ?? items[0];
  await page.goto('/magicshelf');
  await page.locator('#builder .rule-filter-container select').selectOption('title');
  await page.locator('#builder .rule-operator-container select').selectOption('equal');
  await page.locator('#builder .rule-value-container input').fill(selected.title);
  const pending = page.waitForResponse(response => response.url().includes('/magicshelf/preview')
    && response.request().method() === 'POST');
  await page.locator('#preview-btn').click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const result = await response.json() as { success: boolean; count: number; sample_books: string[] };
  expect(result.success).toBe(true);
  expect(result.sample_books).toContain(selected.title);
  const content = page.locator('#preview-content');
  await expect(content.locator('.preview-book-list li')).toHaveText(result.sample_books);
  await expect(content.locator('.preview-book-list li *')).toHaveCount(0);
  await expect(content).toContainText(`${result.count} book(s) match these rules`);
});

for (const status of [200, 400]) {
  test(`rule-preview error text stays literal for HTTP ${status}`, async ({ page }) => {
    const message = '<b data-cwng-display="error">Literal error text</b>';
    // This isolates each protocol-error rendering branch. The matching-book
    // test above separately exercises the actual unmodified endpoint and data.
    await page.route('**/magicshelf/preview', route => route.fulfill({
      status, json: { success: false, message },
    }));
    await page.goto('/magicshelf');
    await page.locator('#builder .rule-filter-container select').selectOption('title');
    await page.locator('#builder .rule-value-container input').fill('Native');
    await page.locator('#preview-btn').click();
    const content = page.locator('#preview-content');
    await expect(content).toHaveText(`Error: ${message}`);
    await expect(content.locator('b[data-cwng-display="error"]')).toHaveCount(0);
    await expect(page.locator('#preview-btn')).toBeEnabled();
  });
}
