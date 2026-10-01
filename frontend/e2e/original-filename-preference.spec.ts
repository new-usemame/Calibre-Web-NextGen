import { test, expect } from './fixtures';

test('original filename visibility defaults on and follows a secondary account', async ({ page: admin, secondaryUser }) => {
  const user = secondaryUser.page;
  const listing = await user.request.get('/api/v1/books?per_page=1');
  expect(listing.ok()).toBeTruthy();
  const book = (await listing.json()).items?.[0] as { id: number; title: string } | undefined;
  if (!book) {
    test.skip(true, 'seed library has no book');
    return;
  }

  const interceptFilename = async (page: import('@playwright/test').Page) => {
    await page.route(`**/api/v1/books/${book.id}`, async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        json: { ...(await response.json()), original_filename: 'source-name-for-this-account.epub' },
      });
    });
  };

  await interceptFilename(user);
  const initialMe = await user.request.get('/api/v1/auth/me').then((response) => response.json());
  expect(initialMe.preferences.show_original_filename).toBeNull();
  await user.goto(`/app/book/${book.id}`);
  await expect(user.getByText('source-name-for-this-account.epub')).toBeVisible();

  await user.goto('/app/account');
  const toggle = user.getByRole('checkbox', { name: /Show original filename/ });
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await expect.poll(async () => {
    const me = await user.request.get('/api/v1/auth/me').then((response) => response.json());
    return me.preferences.show_original_filename;
  }).toBe(false);
  await user.goto(`/app/book/${book.id}`);
  await expect(user.getByText('source-name-for-this-account.epub')).toHaveCount(0);
  await user.goto(`/app/book/${book.id}/edit`);
  await expect(user.getByText('source-name-for-this-account.epub')).toBeVisible();

  await interceptFilename(admin);
  await admin.goto(`/app/book/${book.id}`);
  await expect(admin.getByText('source-name-for-this-account.epub')).toBeVisible();
});
