import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('reader phase 1', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/app');
    const booksResponse = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
    const books = await booksResponse.json();
    const bookPaths = (books.items || books.books || []).map((book: { id: number }) => `/app/book/${book.id}`);
    let readerReady = false;
    for (const bookPath of bookPaths) {
      await page.goto(bookPath);
      const readerPath = await page.locator('a[href*="/read/"]').first().getAttribute('href').catch(() => null);
      if (!readerPath) continue;
      await page.goto(readerPath);
      readerReady = await page.locator('iframe').waitFor({ state: 'visible', timeout: 5_000 })
        .then(() => true).catch(() => false);
      if (readerReady) break;
    }
    test.skip(!readerReady, 'no loadable EPUB reader available in this library');
  });

  test('persists every appearance setting and keeps the mobile drawer in bounds', async ({ page }) => {
    await page.getByRole('button', { name: 'Reading appearance' }).click();
    const dialog = page.getByRole('dialog', { name: 'Reading appearance' });
    await expect(dialog).toBeVisible();

    await page.getByRole('button', { name: 'Dark' }).click();
    await page.getByLabel('Font family').selectOption('Arial');
    await page.getByLabel('Font size').fill('130');
    await page.getByLabel('Page margins').fill('32');
    await page.getByLabel('Line height').fill('190');
    await expect.poll(async () => {
      const saved = await page.request.get('/api/v1/reader/settings');
      return (await saved.json()).reader;
    }).toMatchObject({ theme: 'darkTheme', font: 'Arial', fontSize: 130, margin: 32, lineHeight: 190 });

    const bounds = await dialog.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right, viewport: window.innerWidth };
    });
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(bounds.viewport);

    await page.reload();
    await page.getByRole('button', { name: 'Reading appearance' }).click();
    await expect(page.getByLabel('Font family')).toHaveValue('Arial');
    await expect(page.getByLabel('Font size')).toHaveValue('130');
    await expect(page.getByLabel('Page margins')).toHaveValue('32');
    await expect(page.getByLabel('Line height')).toHaveValue('190');
  });

  test('reader chrome remains AA across all six app theme choices', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === 'mobile', 'the same tokenized chrome is swept once on desktop');
    for (const storedTheme of ['system', 'light', 'dark', 'sepia', 'high-contrast', 'midnight']) {
      const concreteTheme = storedTheme === 'system' ? 'dark' : storedTheme;
      await page.locator('html').evaluate((root, theme) => root.setAttribute('data-theme', theme), concreteTheme);
      const results = await new AxeBuilder({ page }).include('header').analyze();
      expect(
        results.violations.filter((violation) => ['critical', 'serious'].includes(violation.impact || '')),
        `${storedTheme} reader chrome`,
      ).toEqual([]);
    }
  });
});
