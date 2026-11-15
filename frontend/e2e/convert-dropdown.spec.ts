import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/**
 * Convert target dropdown:
 *   - The "to" field is a dropdown, not a free-text input.
 *   - Target options exclude the currently selected source format.
 *   - Submitting the form POSTs the chosen {from, to} pair.
 *
 * The form lives in the book page's Files section (it moved out of Edit
 * metadata with the book-page actions cleanup); labels and aria-labels are
 * unchanged.
 *
 * Seed-resilient: queries the API for a book with at least one source format
 * and one different target format, skipping otherwise.
 */

interface ConvertibleBook {
  id: number;
  source: string;
  target: string;
}

async function findConvertibleBook(page: Page): Promise<ConvertibleBook | null> {
  return page.evaluate(async () => {
    const j = (u: string): Promise<any> =>
      fetch(u, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

    const items = (await j('/api/v1/books?per_page=20'))?.items ?? [];
    for (const item of items) {
      const detail = await j(`/api/v1/books/${item.id}`);
      const sources = detail?.convert_options?.sources ?? [];
      const targets = detail?.convert_options?.targets ?? [];
      if (sources.length && targets.length) {
        const source = sources[0];
        const target = targets.find((t: string) => t.toLowerCase() !== source.toLowerCase());
        if (target) return { id: item.id, source, target };
      }
    }
    return null;
  });
}

test('convert form offers a target dropdown that excludes the source format and posts the chosen pair', async ({ page }) => {
  await page.goto('/app');
  const book = await findConvertibleBook(page);
  test.skip(book == null, 'seed has no book with convertible source/target pair');

  const errors = collectPageErrors(page);

  // Stub the convert endpoint so we don't mutate the seed library.
  let postedBody: Record<string, string> | null = null;
  await page.route(`/api/v1/books/${book!.id}/convert`, async (route, request) => {
    if (request.method() === 'POST') {
      postedBody = request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, message: 'Queued for conversion to MOBI' }),
      });
    } else {
      await route.continue();
    }
  });

  await page.goto(`/app/book/${book!.id}`, { waitUntil: 'domcontentloaded' });

  // The convert form is part of the Files section at the foot of the book
  // page: two selects and a visible "to" separator.
  const files = page.getByTestId('book-files');
  const fromSelect = files.locator('select', { hasText: book!.source.toUpperCase() }).first();
  const toSelect = files.getByLabel('Convert to format');
  await expect(fromSelect).toBeVisible({ timeout: 10_000 });
  await expect(toSelect).toBeVisible({ timeout: 10_000 });
  await expect(files.getByText('to', { exact: true })).toBeVisible();
  await expect(toSelect.locator('option[value=""]')).toHaveText('Select format');

  // The "to" dropdown does not contain the selected source format.
  const toOptions = await toSelect.locator('option').allTextContents();
  expect(toOptions.map((o) => o.toLowerCase())).not.toContain(book!.source.toLowerCase());

  // Choose a target and submit.
  await toSelect.selectOption(book!.target.toUpperCase());
  await files.getByRole('button', { name: 'Convert', exact: true }).click();

  await expect(files.getByText(/Queued for conversion/)).toBeVisible({ timeout: 10_000 });
  expect(postedBody).toEqual({
    from: book!.source.toUpperCase(),
    to: book!.target.toUpperCase(),
  });

  assertNoPageErrors(errors);
});

/**
 * #1110: a queued conversion is followed to its end. The convert API returns
 * the task id; the page watches /api/v1/tasks and, when that task finishes,
 * says the format is ready and shows it in the file list without a refresh.
 * A failed task shows the converter's error instead. Endpoints are stubbed so
 * the seed library is not modified.
 */
for (const outcome of ['finished', 'failed'] as const) {
  test(`a queued conversion reports when it has ${outcome}`, async ({ page }) => {
    await page.goto('/app');
    const book = await findConvertibleBook(page);
    test.skip(book == null, 'seed has no book with convertible source/target pair');
    const target = book!.target.toUpperCase();
    const errors = collectPageErrors(page);
    const taskId = 'e2e-1110-task';
    let queued = false;
    let polls = 0;

    await page.route(`/api/v1/books/${book!.id}/convert`, async (route) => {
      queued = true;
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, message: `Queued for conversion to ${target}`, task_id: taskId }),
      });
    });
    await page.route('/api/v1/tasks', async (route) => {
      polls += 1;
      const ended = outcome === 'finished' ? 3 : 1;
      const stat = queued && polls > 1 ? ended : 2;
      const items = queued ? [{
        task_id: taskId, taskMessage: 'Convert', progress: '50 %', user: 'admin',
        is_cancellable: false, stat, error: stat === 1 ? 'ebook-convert exited with 1' : null,
      }] : [];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items }) });
    });
    let converted = false;
    await page.route(`/api/v1/books/${book!.id}`, async (route) => {
      const resp = await route.fetch();
      const body = await resp.json();
      if (converted) body.formats = [...body.formats, { ...body.formats[0], format: target }];
      await route.fulfill({ response: resp, json: body });
    });

    await page.goto(`/app/book/${book!.id}`, { waitUntil: 'domcontentloaded' });
    const files = page.getByTestId('book-files');
    const toSelect = files.getByLabel('Convert to format');
    await expect(toSelect).toBeVisible({ timeout: 10_000 });
    await toSelect.selectOption(target);
    converted = outcome === 'finished';
    await files.getByRole('button', { name: 'Convert', exact: true }).click();
    await expect(files.getByText(/Queued for conversion/)).toBeVisible({ timeout: 10_000 });

    if (outcome === 'finished') {
      await expect(files.getByText(`${target} is ready.`)).toBeVisible({ timeout: 15_000 });
      await expect(files.getByText(target, { exact: true }).first()).toBeVisible();
    } else {
      await expect(files.getByText('ebook-convert exited with 1')).toBeVisible({ timeout: 15_000 });
    }
    assertNoPageErrors(errors);
  });
}
