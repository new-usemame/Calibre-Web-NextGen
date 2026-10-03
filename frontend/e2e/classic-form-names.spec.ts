import { test, expect } from '@playwright/test';

async function firstBookId(page: import('@playwright/test').Page) {
  const response = await page.request.get('/api/v1/books?limit=1');
  expect(response.status()).toBe(200);
  const data = await response.json() as { books?: Array<{ id: number }>; items?: Array<{ id: number }> };
  const book = (data.books ?? data.items)?.[0];
  expect(book, 'the real fixture library needs a book').toBeTruthy();
  return book!.id;
}

test('Classic editor exposes author and icon-button names', async ({ page }) => {
  await page.goto(`/admin/book/${await firstBookId(page)}`);
  await expect(page.locator('#authors')).toHaveAccessibleName('Author');
  await expect(page.locator('#xchange')).toHaveAccessibleName('Exchange author & title');
  await expect(page.locator('#pubdate_delete')).toHaveAccessibleName('Delete: Published Date');
  await page.locator('#title').fill('Title field probe');
  await page.locator('#authors').fill('Author field probe');
  await page.locator('#xchange').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#title')).toHaveValue('Author field probe');
  await expect(page.locator('#authors')).toHaveValue('Title field probe');
  // Leave without saving; the probe only changes this form.
});

test('localized date mirrors stay out of keyboard and accessibility navigation', async ({ page }) => {
  await page.goto(`/admin/book/${await firstBookId(page)}`);
  await page.locator('#pubdate').fill('2020-05-03');
  await page.locator('#publisher').focus();
  const mirror = page.locator('#fake_pubdate');
  await expect(mirror).toBeVisible();
  await expect(mirror).toHaveAttribute('aria-hidden', 'true');
  await expect(mirror).toHaveAttribute('tabindex', '-1');
  await expect(page.locator('#pubdate')).toHaveAccessibleName('Published Date');
  await page.locator('#pubdate').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('#pubdate_delete')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#pubdate')).toHaveValue('');
  await expect(mirror).toBeHidden();
});

test('Classic bulk metadata dialog associates every visible field label', async ({ page }) => {
  await page.goto('/table');
  const rows = page.locator('#books-table tbody tr:not(.no-records-found)');
  await expect(rows.first()).toBeVisible();
  await rows.first().locator('td.bs-checkbox input[type="checkbox"]').check();
  await page.locator('#edit_selected_books').click();
  const modal = page.locator('#edit_selected_modal');
  await expect(modal).toBeVisible();
  await expect(modal).toHaveAccessibleName('Edit Metadata');
  for (const [id, name] of [
    ['title_input', 'Title'], ['title_sort_input', 'Title Sort'],
    ['author_sort_input', 'Author Sort'], ['authors_input', 'Authors'],
    ['categories_input', 'Tags'], ['series_input', 'Series'],
    ['languages_input', 'Languages'], ['publishers_input', 'Publishers'],
    ['comments_input', 'Description'],
  ]) {
    await expect(modal.locator(`#${id}`)).toHaveAccessibleName(name);
    await modal.getByText(name, { exact: true }).click();
    await expect(modal.locator(`#${id}`)).toBeFocused();
  }
  await modal.locator('#edit_selected_abort').click();
  await expect(modal).toBeHidden();
});
