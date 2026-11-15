import { test, expect } from './fixtures';
import type { Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function capture(page: Page, info: TestInfo, name: string, classic = false) {
  const original = await page.locator('html').getAttribute('data-theme');
  for (const theme of classic ? ['caliblur'] : ['light', 'dark']) {
    if (!classic) await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    const path = info.outputPath(`${name}-${info.project.name}-${theme}.jpg`);
    await page.screenshot({ path, type: 'jpeg', quality: 72, animations: 'disabled' });
    await info.attach(`${name}-${theme}`, { path, contentType: 'image/jpeg' });
  }
  if (original) await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, original);
}


test('original filename visibility defaults on and follows a secondary account', async ({ page: admin, secondaryUser }, testInfo) => {
  const user = secondaryUser.page;
  await user.setViewportSize(testInfo.project.name === 'mobile' ? { width: 375, height: 667 } : { width: 1280, height: 800 });
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
  await user.getByText('source-name-for-this-account.epub').scrollIntoViewIfNeeded();
  await capture(user, testInfo, 'book-filename-visible');

  await user.goto('/app/account');
  const toggle = user.getByRole('checkbox', { name: /Show original filename/ });
  await expect(toggle).toBeChecked();
  await toggle.scrollIntoViewIfNeeded();
  await capture(user, testInfo, 'account-filename-setting');
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  const violations = (await new AxeBuilder({ page: user }).include('main').analyze()).violations;
  expect(violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
  await expect.poll(async () => {
    const me = await user.request.get('/api/v1/auth/me').then((response) => response.json());
    return me.preferences.show_original_filename;
  }).toBe(false);
  await user.goto(`/app/book/${book.id}`);
  await expect(user.getByText('source-name-for-this-account.epub')).toHaveCount(0);
  await user.locator('dl').first().scrollIntoViewIfNeeded();
  await capture(user, testInfo, 'book-filename-hidden');
  // Give only this owned test account the authority a metadata manager has.
  const csrf = (await (await admin.request.get('/api/v1/auth/csrf')).json()).csrf_token;
  expect((await admin.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
    headers: { 'X-CSRFToken': csrf }, data: { roles: { viewer: true, download: true, edit: true } },
  })).ok()).toBeTruthy();
  await user.goto(`/app/book/${book.id}/edit`);
  await expect(user.getByText('source-name-for-this-account.epub')).toBeVisible();

  await user.goto('/me');
  const classicToggle = user.locator('#show_original_filename');
  await expect(classicToggle).not.toBeChecked();
  await classicToggle.scrollIntoViewIfNeeded();
  await capture(user, testInfo, 'classic-filename-setting', true);
  await classicToggle.check();
  await user.locator('#user_submit').click();
  await expect.poll(async () => (await (await user.request.get('/api/v1/auth/me')).json()).preferences.show_original_filename).toBe(true);
  await user.goto('/app/account');
  await expect(toggle).toBeChecked();

  await interceptFilename(admin);
  await admin.goto(`/app/book/${book.id}`);
  await expect(admin.getByText('source-name-for-this-account.epub')).toBeVisible();
});
