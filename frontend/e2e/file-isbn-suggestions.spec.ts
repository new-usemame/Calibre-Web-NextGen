import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Mock the wire, exercise the real editor: a scan must never submit metadata;
// using a candidate changes only ISBN aliases, and failure retains the draft.
test('file ISBN candidates require an explicit choice and metadata save', async ({ page }) => {
  const id = 9523;
  let writes = 0;
  let fail = false;
  let saved: unknown;
  const meta = { id, title: 'An edition to confirm', authors: 'Fixture Author', series: '', series_index: '', tags: '', publishers: '', languages: '', comments: '', rating: 0, pubdate: '', custom_columns: [], identifiers: [
    { type: 'isbn_10', val: '0306406152' }, { type: 'isbn13', val: '9780140328721' }, { type: 'doi', val: '10.1234/preserved' },
  ] };
  await page.route(`**/api/v1/books/${id}`, route => route.fulfill({ json: { id, title: meta.title, authors: [{id: 1, name: meta.authors}], tags: [], languages: [], publishers: [], identifiers: [], formats: [], read: false, archived: false } }));
  await page.route(`**/api/v1/books/${id}/metadata`, route => {
    if (route.request().method() === 'POST') { writes++; saved = route.request().postDataJSON(); }
    return route.fulfill({ json: meta });
  });
  await page.route(`**/api/v1/books/${id}/isbn-candidates`, route => fail
    ? route.fulfill({ status: 422, json: { error: { code: 'scan_failed', message: 'failed' } } })
    : route.fulfill({ json: { available: true, candidates: [{ isbn: '9780306406157', context: 'Copyright: <script>literal only</script> ISBN 978-0-306-40615-7', format: 'epub' }], scanned_formats: ['EPUB'], unsupported_formats: ['MOBI'], unavailable_formats: [], failed_formats: [], truncated: false } }));
  await page.goto(`/app/book/${id}/edit`);
  const box = page.getByRole('region', { name: 'ISBN suggestions from book files' });
  await box.getByRole('button', { name: 'Find ISBN in book files' }).click();
  await expect(box.getByRole('status')).toContainText('ISBN suggestions found: 1.');
  await expect(box).toContainText('<script>literal only</script>');
  expect(writes).toBe(0);
  await expect(page.getByLabel('Identifier value', { exact: true }).first()).toHaveValue('0306406152');
  await box.getByRole('button', { name: 'Use ISBN 9780306406157' }).click();
  await expect(page.getByLabel('Identifier value', { exact: true })).toHaveCount(2);
  await expect(page.getByLabel('Identifier value', { exact: true }).nth(0)).toHaveValue('10.1234/preserved');
  await expect(page.getByLabel('Identifier value', { exact: true }).nth(1)).toHaveValue('9780306406157');
  expect(writes).toBe(0);
  fail = true;
  await box.getByRole('button', { name: 'Find ISBN in book files' }).click();
  await expect(box.getByRole('status')).toContainText('Could not scan book files. Please try again.');
  await expect(page.getByLabel('Identifier value', { exact: true }).nth(1)).toHaveValue('9780306406157');
  const axe = await new AxeBuilder({ page }).include('[aria-label="ISBN suggestions from book files"]').analyze();
  expect(axe.violations.filter(v => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect.poll(() => writes).toBe(1);
  expect((saved as {identifiers: unknown}).identifiers).toEqual([{ type: 'doi', val: '10.1234/preserved' }, { type: 'isbn', val: '9780306406157' }]);
});
