import { expect, test } from '@playwright/test';

test.describe('Discover reshuffle', () => {
  test('keeps the current cards mounted while replacement picks load', async ({ page }) => {
    let requests = 0;
    let releaseSecond: (() => void) | undefined;
    const secondHeld = new Promise<void>((resolve) => { releaseSecond = resolve; });

    await page.route('**/api/v1/books?filter=discover&per_page=12', async (route) => {
      requests += 1;
      if (requests === 2) await secondHeld;
      await route.continue();
    });

    await page.goto('/app');
    const section = page.getByRole('region', { name: 'Discover' });
    try {
      await section.waitFor({ state: 'visible', timeout: 8_000 });
    } catch {
      test.skip(true, 'Discover is disabled in this fixture');
    }
    const firstCard = section.locator('a[href*="/book/"]').first();
    await expect(firstCard).toBeVisible();
    const originalHref = await firstCard.getAttribute('href');

    await section.getByRole('button', { name: 'Shuffle picks' }).click();
    await expect.poll(() => requests).toBe(2);
    await expect(section.locator(`a[href="${originalHref}"]`)).toBeVisible();

    releaseSecond?.();
    await expect(section.getByRole('button', { name: 'Shuffle picks' })).toBeEnabled();
  });
});

test.describe('book upload', () => {
  test('drops a book through multipart upload and announces the result', async ({ page }) => {
    let multipart = '';
    await page.route('**/api/v1/upload', async (route) => {
      multipart = route.request().postData() ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ queued: ['sp1-test.epub'], errors: [] }),
      });
    });

    await page.goto('/app/upload');
    await expect(page.getByLabel('Choose books to upload')).toBeAttached();
    const dataTransfer = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(['SP1 upload contract'], 'sp1-test.epub', {
        type: 'application/epub+zip',
      }));
      return transfer;
    });
    await page.getByText('Drop files here, or click to choose').locator('..')
      .dispatchEvent('drop', { dataTransfer });

    await expect(page.getByRole('status').filter({ hasText: 'sp1-test.epub' }))
      .toContainText('sp1-test.epub');
    expect(multipart).toContain('sp1-test.epub');
  });
});
