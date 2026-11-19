import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  return { 'X-CSRFToken': (await response.json()).csrf_token as string };
}

for (const surface of ['new UI', 'classic'] as const) {
  test(`${surface}: admin starts an empty library and the user adds one book`, async ({
    page, secondaryUser,
  }, testInfo) => {
    const grant = await page.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
      headers: await csrf(page), data: { roles: { browse_global: true } },
    });
    expect(grant.ok(), await grant.text()).toBeTruthy();
    expect(await grant.json()).toMatchObject({ library_mode: 'monolibrary', my_library_seeded: false });

    if (surface === 'new UI') {
      await page.goto('/app/admin');
      const card = page.locator('section').filter({
        has: page.getByText(secondaryUser.username, { exact: true }),
      });
      await card.getByRole('checkbox', { name: /^Start My Library empty/ }).check();
      await card.screenshot({ path: testInfo.outputPath('empty-start-selected.png') });
      const dialog = page.waitForEvent('dialog').then(async confirmation => {
        expect(confirmation.message()).toContain('an empty My Library');
        await confirmation.accept();
      });
      await Promise.all([
        dialog,
        card.getByRole('radio', { name: /^My Library/ }).click(),
      ]);
      await expect(card.getByRole('radio', { name: /^My Library/ })).toBeChecked();
      await expect(card.getByRole('checkbox', { name: /^Start My Library empty/ })).toHaveCount(0);
    } else {
      await page.goto(`/admin/user/${secondaryUser.id}`);
      await page.locator('#library_mode_personal').check();
      await page.getByRole('checkbox', { name: 'Start My Library empty', exact: true }).check();
      await page.locator('#library_start_empty').screenshot({ path: testInfo.outputPath('empty-start-selected.png') });
      await page.locator('#user_submit').click();
      await expect(page.locator('#library_start_empty')).toHaveCount(0);
      await expect(page.locator('#library_mode_personal')).toBeChecked();
    }

    const reader = secondaryUser.page;
    const me = await reader.request.get('/api/v1/auth/me');
    expect(await me.json()).toMatchObject({ library_mode: 'personal_library', my_library_seeded: true });
    const empty = await reader.request.get('/api/v1/books?per_page=200');
    expect(empty.ok()).toBeTruthy();
    expect((await empty.json()).items).toEqual([]);
    const global = await reader.request.get('/api/v1/library/global?per_page=10');
    expect(global.ok()).toBeTruthy();
    const books = (await global.json()).items as Array<{ id: number }>;
    expect(books.length).toBeGreaterThan(0);
    const add = await reader.request.put(`/api/v1/books/${books[0].id}/my-library`, {
      headers: await csrf(reader),
    });
    expect(add.ok(), await add.text()).toBeTruthy();
    const selected = await reader.request.get('/api/v1/books?per_page=200');
    expect((await selected.json()).items.map((book: { id: number }) => book.id)).toEqual([books[0].id]);
  });
}
