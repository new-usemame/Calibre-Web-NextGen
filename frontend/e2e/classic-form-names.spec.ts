import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { collectPageErrors } from './utils';

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
  await expect(page.locator('#pubdate_delete')).toHaveAccessibleName('Remove Published Date');
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
  // Automatic sorting intentionally disables these fields. Switch off the
  // actual controls before testing manual sort-label activation.
  await page.locator('#autoupdate_titlesort').uncheck();
  await page.locator('#autoupdate_authorsort').uncheck();
  await page.locator('#edit_selected_books').click();
  const modal = page.locator('#edit_selected_modal');
  await expect(modal).toBeVisible();
  await expect(modal).toHaveAccessibleName('Edit Metadata');
  for (const [id, name] of [
    ['title_input', 'Title'], ['title_sort_input', 'Title Sort'],
    ['author_sort_input', 'Author Sort'], ['authors_input', 'Authors'],
    ['categories_input', 'Tags'], ['series_input', 'Series'],
    ['languages_input', 'Languages'], ['publishers_input', 'Publishers'],
    ['comments_input', 'Comments'],
  ]) {
    await expect(modal.locator(`#${id}`)).toHaveAccessibleName(name);
    await modal.getByText(name, { exact: true }).click();
    await expect(modal.locator(`#${id}`)).toBeFocused();
  }
  await modal.locator('#edit_selected_abort').click();
  await expect(modal).toBeHidden();
});


test('rich-text breadcrumbs retain block selection with valid button semantics', async ({ page }) => {
  await page.goto(`/admin/book/${await firstBookId(page)}`);
  await expect(page.locator('#comments_ifr')).toBeVisible();
  await page.evaluate(() => {
    const editor = (window as unknown as { tinymce: { get(id: string): { setContent(content: string): void } } }).tinymce.get('comments');
    editor.setContent('<p><strong>Nested editor probe</strong></p>');
  });
  await page.frameLocator('#comments_ifr').getByText('Nested editor probe', { exact: true }).click();
  const pathSelector = '.tox:has(#comments_ifr) .tox-statusbar__path';
  const path = page.locator(pathSelector);
  await expect(path.getByRole('button', { name: 'p', exact: true })).toBeVisible();
  await path.getByRole('button', { name: 'p', exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { tinymce: { get(id: string): { selection: { getNode(): Element } } } }).tinymce.get('comments').selection.getNode().nodeName)).toBe('P');
  await expect(path.getByRole('button', { name: 'p', exact: true })).toBeVisible();
  const result = await new AxeBuilder({ page }).include(pathSelector).withRules(['aria-allowed-attr']).analyze();
  expect(result.violations, 'retained breadcrumb buttons must use supported ARIA').toEqual([]);
});


test('a focused Classic date shows the value being edited', async ({ page }) => {
  await page.goto(`/admin/book/${await firstBookId(page)}`);
  const date = page.locator('#pubdate');
  const mirror = page.locator('#fake_pubdate');
  await date.focus();
  await expect(mirror, 'decorative localized text must not cover the editable value').toBeHidden();
  expect(await date.evaluate(el => getComputedStyle(el).color)).not.toBe('rgba(0, 0, 0, 0)');
  await date.fill('');
  await date.pressSequentially('2020-05-03');
  await expect(date).toHaveValue('2020-05-03');
  await date.press('Tab');
  await expect(mirror).toBeVisible();
  await expect(mirror).toHaveValue('5/3/2020');
});

test('Classic date blur displays one localized value and keeps raw invalid input readable', async ({ page }) => {
  await page.goto(`/admin/book/${await firstBookId(page)}`);
  const date = page.locator('#pubdate');
  const mirror = page.locator('#fake_pubdate');
  await page.locator('#publisher').focus();
  await expect(mirror).toBeVisible();
  await expect(date).toHaveCSS('color', 'rgba(0, 0, 0, 0)');
  await date.focus();
  await date.fill('');
  await date.pressSequentially('not a date');
  await date.press('Tab');
  await expect(date).toHaveValue('not a date');
  await expect(mirror).toBeHidden();
  expect(await date.evaluate(el => getComputedStyle(el).color)).not.toBe('rgba(0, 0, 0, 0)');
});


test('Classic advanced-search date controls share named canonical fields', async ({ page }) => {
  await page.goto('/advsearch');
  await expect(page.locator('#publisher')).toHaveAccessibleName('Publisher');
  for (const [id, name] of [['publishstart', 'Published Date From'], ['publishend', 'Published Date To']]) {
    const date = page.locator(`#${id}`);
    const mirror = page.locator(`#fake_${id}`);
    await expect(date).toHaveAccessibleName(name);
    await expect(mirror).toHaveAttribute('aria-hidden', 'true');
    await expect(mirror).toHaveAttribute('tabindex', '-1');
    await expect(page.locator(`#${id}_delete`)).toHaveAccessibleName(`Remove ${name}`);
    await date.fill('');
    await date.pressSequentially('2020-05-03');
    await expect(mirror).toBeHidden();
    await date.press('Tab');
    await expect(page.locator(`#${id}_delete`)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(date).toHaveValue('');
    await expect(mirror).toBeHidden();
  }
});


// Use the real authenticated HTML/backend, with a deliberately restrictive
// response header. No policy opt-in or console filtering can mask the failure.
test('Chromium Classic editor works with unload denied', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'This control requires Chromium unload Permissions Policy support');
  const errors = collectPageErrors(page);
  const editorPath = `/admin/book/${await firstBookId(page)}`;
  await page.route(`**${editorPath}`, async route => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: {
      ...response.headers(), 'permissions-policy': 'unload=()',
    } });
  });
  await page.goto(editorPath);
  expect(await page.evaluate(() => (document as Document & {
    featurePolicy?: { allowsFeature(feature: string): boolean };
  }).featurePolicy?.allowsFeature('unload'))).toBe(false);
  await expect(page.locator('#comments_ifr')).toBeVisible();
  const firstBody = page.frameLocator('#comments_ifr').locator('body');
  await firstBody.fill('Unsubmitted policy probe');
  await expect(firstBody).toContainText('Unsubmitted policy probe');
  // Leave this real iframe, then query/edit a newly initialized editor. No
  // shared fixture metadata is saved by this CI regression.
  await page.goto('/table');
  await expect(page.locator('#books-table')).toBeVisible();
  await page.goto(editorPath);
  await expect(page.locator('#comments_ifr')).toBeVisible();
  const nextBody = page.frameLocator('#comments_ifr').locator('body');
  await nextBody.fill('Second unsubmitted policy probe');
  await expect(nextBody).toContainText('Second unsubmitted policy probe');
  expect(errors, `unfiltered editor console/page errors: ${errors.join('\n')}`).toEqual([]);
});
