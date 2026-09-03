import { expect, test, type Page } from '@playwright/test';

const FAILURE_TITLE = process.env.E2E_BULK_EDIT_FAILURE_TITLE;
const SUCCESS_TITLE = process.env.E2E_BULK_EDIT_SUCCESS_TITLE;

async function selectBookThroughCheckbox(page: Page, title: string) {
  const matchingRows = page.locator('#books-table tbody tr:not(.no-records-found)').filter({
    has: page.getByRole('link', { name: title, exact: true }),
  });
  await expect(matchingRows, `the legacy table should find exactly one book named "${title}"`).toHaveCount(1);

  const checkbox = matchingRows.locator('td.bs-checkbox input[type="checkbox"]');
  await expect(checkbox).toBeVisible();
  await checkbox.check();
}

test('legacy bulk edit renders the server partial-failure message (#2073)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the legacy callback is viewport-independent');
  test.skip(
    !FAILURE_TITLE || !SUCCESS_TITLE,
    'requires E2E_BULK_EDIT_FAILURE_TITLE and E2E_BULK_EDIT_SUCCESS_TITLE on a fault-injected instance',
  );

  await page.goto('/table');
  await expect(page.getByRole('heading', { name: 'Books List' })).toBeVisible();

  await selectBookThroughCheckbox(page, FAILURE_TITLE!);
  await selectBookThroughCheckbox(page, SUCCESS_TITLE!);

  const editSelected = page.locator('#edit_selected_books');
  await expect(editSelected).not.toHaveClass(/\bdisabled\b/);
  await editSelected.click();

  const modal = page.locator('#edit_selected_modal');
  await expect(modal).toBeVisible();
  await modal.locator('#title_input').fill('E2E partial-failure title');

  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname.endsWith('/ajax/editselectedbooks'));
  await modal.locator('#edit_selected_confirm').click();
  const response = await responsePromise;
  expect(response.ok(), 'the route reports per-book failure in JSON, not as an HTTP error').toBe(true);

  const payload = await response.json() as {
    success: boolean;
    partial: boolean;
    successful_books: number[];
    failed_books: Array<{
      book_id: number;
      stage: string;
      files_may_be_inconsistent: boolean;
    }>;
    message: string;
  };
  expect(payload.success).toBe(false);
  expect(payload.partial).toBe(true);
  expect(payload.successful_books).toHaveLength(1);
  expect(payload.failed_books).toEqual([
    expect.objectContaining({
      stage: 'rename',
      files_may_be_inconsistent: true,
    }),
  ]);
  expect(payload.message).toMatch(
    /^Bulk edit completed with errors: 1 succeeded and 1 failed \(book IDs: \d+\)\./,
  );

  const dangerBanner = page.locator('#flash_danger');
  await expect(dangerBanner).toBeVisible();
  await expect(dangerBanner).toHaveText(payload.message);
});
