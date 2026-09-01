import { expect, Page, test } from '@playwright/test';

const TAG_LINKS = '#book-tags a[href*="/tags/"]';

async function firstBookId(page: Page): Promise<number | null> {
  await page.goto('/app');
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', {
      headers: { Accept: 'application/json' },
    }).catch(() => null);
    if (!response?.ok) return null;
    return (await response.json())?.items?.[0]?.id ?? null;
  });
}

async function showBookWithTags(page: Page, bookId: number, count: number) {
  await page.route(`**/api/v1/books/${bookId}`, async (route) => {
    const response = await route.fetch();
    const book = await response.json();
    book.tags = Array.from({ length: count }, (_, index) => ({
      id: 992_080_000 + index,
      name: `Issue 2080 tag ${String(index + 1).padStart(2, '0')}`,
    }));
    await route.fulfill({ response, json: book });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('main h1')).toBeVisible({ timeout: 10_000 });
}

test('desktop shows all nine tags without a disclosure (#2080)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop project only');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  await showBookWithTags(page, bookId!, 9);

  await expect(page.locator(TAG_LINKS)).toHaveCount(9);
  await expect(page.getByRole('button', { name: /Show all \d+ tags/ })).toHaveCount(0);
});

test('mobile still collapses and expands a genuinely long tag list (#2080)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'mobile project only');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  await showBookWithTags(page, bookId!, 25);

  const disclosure = page.getByRole('button', { name: 'Show all 25 tags' });
  await expect(page.locator(TAG_LINKS)).toHaveCount(8);
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  await expect(disclosure).toHaveAttribute('aria-controls', 'book-tags');

  await disclosure.click();

  await expect(page.locator(TAG_LINKS)).toHaveCount(25);
  const showFewer = page.getByRole('button', { name: 'Show fewer tags' });
  await expect(showFewer).toHaveAttribute('aria-expanded', 'true');
  await expect(showFewer).toHaveAttribute('aria-controls', 'book-tags');
});
