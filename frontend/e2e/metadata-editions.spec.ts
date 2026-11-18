import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { test, expect, type Page } from '@playwright/test';
import JSZip from 'jszip';
import type { MetaResult } from '../src/lib/api';
import { assertNoHorizontalOverflow } from './utils';

/*
 * Hardcover "Editions" drill-down + "View all details" overlay on the metadata
 * fetch flow (EditBook). Restores parity with the classic UI's Editions link so
 * a user can pick the specific edition whose identifiers Hardcover progress-sync
 * needs (Discord report: mgrimace — multiple hardcover editions per book).
 *
 * /metadata/search is mocked so this runs in CI without a live Hardcover token.
 * Two query shapes are served: the title search, and the `hardcover-id:<id>`
 * edition search the drill-down issues.
 */

const HARDCOVER_SOURCE = { id: 'hardcover', description: 'Hardcover', link: 'https://hardcover.app/' };

// Title-level results: one Hardcover hit (offers Editions), one non-Hardcover
// hit (must NOT offer Editions).
const TITLE_RESULTS: MetaResult[] = [
  {
    title: 'The Picture of Dorian Gray',
    authors: ['Oscar Wilde'],
    cover: '',
    description: 'A long description that the compact row truncates but the details overlay shows in full.',
    tags: ['Classics', 'Fiction', 'Gothic'],
    publisher: 'Hardcover Books',
    publishedDate: '1890-01-01',
    identifiers: { 'hardcover-id': '436692', 'hardcover-slug': 'the-picture-of-dorian-gray' },
    source: HARDCOVER_SOURCE,
  },
  {
    title: 'The Picture of Dorian Gray',
    authors: ['Oscar Wilde'],
    cover: '',
    identifiers: { openlibrary: 'OL123M' },
    publisher: 'Penguin',
    publishedDate: '2021',
    source: { id: 'openlibrary', description: 'Open Library' },
  },
];

// Edition results for `hardcover-id:436692`: two real editions (each carrying a
// `hardcover-edition` identifier) plus one noise row from another provider that
// the UI must filter out of the editions view.
const EDITION_RESULTS = [
  {
    title: 'The Picture of Dorian Gray (Paperback)',
    authors: ['Oscar Wilde'],
    cover: '',
    format: 'Physical Book',
    publisher: 'Penguin Classics',
    publishedDate: '2021-10-26',
    identifiers: { 'hardcover-id': '436692', 'hardcover-slug': 'the-picture-of-dorian-gray', 'hardcover-edition': '32701290', isbn: '9789815204230' },
    source: HARDCOVER_SOURCE,
  },
  {
    title: 'The Picture of Dorian Gray (E-Book)',
    authors: ['Oscar Wilde'],
    cover: '',
    format: 'E-Book',
    publisher: 'Cornerstone Digital',
    publishedDate: '2010-07-18',
    identifiers: { 'hardcover-id': '436692', 'hardcover-slug': 'the-picture-of-dorian-gray', 'hardcover-edition': '99999999', isbn: '9780000000001' },
    source: HARDCOVER_SOURCE,
  },
  {
    // Noise: a colon query still fans out to every provider; this row has no
    // `hardcover-edition`, so the editions view must drop it.
    title: 'hardcover-id:436692 (junk match)',
    authors: [],
    cover: '',
    identifiers: { google: 'xyz' },
    source: { id: 'google', description: 'Google', link: 'https://books.google.com' },
  },
];

async function mockSearch(page: Page, opts: { editionDelayMs?: number; titleResults?: MetaResult[] } = {}) {
  await page.route('**/metadata/search', async (route) => {
    const body = route.request().postData() || '';
    const isEditionQuery = decodeURIComponent(body).includes('hardcover-id:');
    if (isEditionQuery && opts.editionDelayMs) {
      await new Promise((r) => setTimeout(r, opts.editionDelayMs));
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results: isEditionQuery ? EDITION_RESULTS : opts.titleResults ?? TITLE_RESULTS,
        providers: [{ id: 'hardcover', name: 'Hardcover', status: 'ok', count: 2, message: '' }],
      }),
    });
  });
}

async function searchFromEditor(page: Page) {
  await page.getByRole('button', { name: /fetch metadata from web/i }).click();
  await page.getByRole('button', { name: /^Search$/ }).click();
}

/** Open the edit page of the first book in the grid and reveal the fetch panel. */
async function openFetchPanel(page: Page) {
  await page.goto('/app');
  const firstBook = page.locator('a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible();
  const href = await firstBook.getAttribute('href');
  const id = (href || '').match(/\/book\/(\d+)/)?.[1];
  expect(id, 'could not resolve a book id from the grid').toBeTruthy();

  await page.goto(`/app/book/${id}/edit`);
  await searchFromEditor(page);
  await expect(page.getByText('Hardcover Books')).toBeVisible();
}

test('Editions button appears only on Hardcover results', async ({ page }) => {
  await mockSearch(page);
  await openFetchPanel(page);

  // Exactly one Hardcover title result → exactly one Editions affordance.
  await expect(page.getByRole('button', { name: /^Editions$/ })).toHaveCount(1);
  // Every result offers "View all details".
  expect(await page.getByRole('button', { name: 'View all details' }).count()).toBeGreaterThanOrEqual(2);
});

test('Editions drill-down lists editions, filters noise, and Back restores results', async ({ page }) => {
  await mockSearch(page);
  await openFetchPanel(page);

  await page.getByRole('button', { name: /^Editions$/ }).click();

  // Editions header + the two real editions; the non-Hardcover noise row is gone.
  await expect(page.getByText('Back to results')).toBeVisible();
  await expect(page.getByText('The Picture of Dorian Gray (Paperback)')).toBeVisible();
  await expect(page.getByText('The Picture of Dorian Gray (E-Book)')).toBeVisible();
  await expect(page.getByText('junk match')).toHaveCount(0);
  // Format is surfaced (on the meta line) so editions are distinguishable.
  await expect(page.getByText(/Physical Book · Penguin Classics/)).toBeVisible();
  await expect(page.getByText(/E-Book · Cornerstone Digital/)).toBeVisible();
  // No recursive drill-down inside the editions view.
  await expect(page.getByRole('button', { name: /^Editions$/ })).toHaveCount(0);
  // The action buttons must reflow, not push the layout wide (mobile-reflow guard).
  await assertNoHorizontalOverflow(page);

  // Back returns to the title results (Editions button is back).
  await page.getByRole('button', { name: /back to results/i }).click();
  await expect(page.getByRole('button', { name: /^Editions$/ })).toHaveCount(1);
});

test('applying an edition merges its identifiers into the form', async ({ page }) => {
  await mockSearch(page);
  await openFetchPanel(page);

  await page.getByRole('button', { name: /^Editions$/ }).click();
  const paperback = page.getByRole('listitem').filter({ hasText: 'The Picture of Dorian Gray (Paperback)' });
  await paperback.getByRole('button', { name: /choose fields/i }).click();
  await expect(paperback.getByRole('checkbox', { name: 'Published 2021-10-26' })).toBeChecked();
  await paperback.getByRole('button', { name: /apply selected/i }).click();
  await expect(page.getByLabel('Published', { exact: true })).toHaveValue('2021-10-26');

  // The chosen edition's identifiers now populate the form's identifiers table.
  const idents = await page.evaluate(() => {
    const types = [...document.querySelectorAll('input[aria-label="Identifier type"]')].map((i) => (i as HTMLInputElement).value);
    const vals = [...document.querySelectorAll('input[aria-label="Identifier value"]')].map((i) => (i as HTMLInputElement).value);
    return types.map((t, i) => ({ type: t, val: vals[i] }));
  });
  expect(idents).toContainEqual({ type: 'hardcover-edition', val: '32701290' });
  expect(idents).toContainEqual({ type: 'isbn', val: '9789815204230' });
});

test('publication dates use provider precision and only apply when selected', async ({ page }) => {
  const dates = [
    { title: 'Month only', publishedDate: ' 2020-02 ', preview: '2020-02', value: '2020-02-01' },
    { title: 'Timestamp with offset', publishedDate: '2020-02-29T23:30:00-05:00', preview: '2020-02-29', value: '2020-02-29' },
    { title: 'Missing date', publishedDate: null },
    { title: 'Impossible date', publishedDate: '2021-02-29' },
    { title: 'Malformed date', publishedDate: '2021-10-26Tgarbage' },
    { title: 'Sentinel date', publishedDate: '0101-01-01' },
  ];
  await mockSearch(page, {
    titleResults: [...TITLE_RESULTS, ...dates.map(({ title, publishedDate }) => ({ title, publishedDate, authors: [], cover: '' }))],
  });
  await openFetchPanel(page);

  const published = page.getByLabel('Published', { exact: true });
  await published.fill('1980-05-06');
  const openLibrary = page.getByRole('listitem').filter({ hasText: 'Penguin' });
  await openLibrary.getByRole('button', { name: /choose fields/i }).click();
  const year = openLibrary.getByRole('checkbox', { name: 'Published 2021' });
  await expect(year).toBeChecked();
  await year.uncheck();
  await openLibrary.getByRole('button', { name: /apply selected/i }).click();
  await expect(published).toHaveValue('1980-05-06');
  await openLibrary.getByRole('button', { name: /choose fields/i }).click();
  await year.check();
  await openLibrary.getByRole('button', { name: /apply selected/i }).click();
  await expect(published).toHaveValue('2021-01-01');

  let expectedDate = '2021-01-01';
  for (const { title, preview, value } of dates) {
    const row = page.getByRole('listitem').filter({ hasText: title });
    await row.getByRole('button', { name: /choose fields/i }).click();
    if (preview && value) {
      await expect(row.getByRole('checkbox', { name: `Published ${preview}` })).toBeChecked();
      expectedDate = value;
    } else {
      await expect(row.getByRole('checkbox', { name: /^Published/ })).toHaveCount(0);
    }
    await row.getByRole('button', { name: /apply selected/i }).click();
    await expect(published).toHaveValue(expectedDate);
  }
});

test('saving a fetched publication year persists it when the editor reopens', async ({ page }) => {
  test.setTimeout(90_000);
  // Import an owned book so desktop, mobile, and other specs can save in parallel.
  const uuid = randomUUID();
  const title = `Publication date fixture ${uuid}`;
  const zip = await JSZip.loadAsync(await readFile('../tests/fixtures/sample_books/reader_lookup_2400.epub'));
  const packagePath = 'EPUB/package.opf';
  const opf = await zip.file(packagePath)!.async('string');
  zip.file(packagePath, opf.replace('urn:uuid:cwng-2400-reader-lookup-fixture', `urn:uuid:${uuid}`)
    .replace('<dc:title>CWNG 2400 Reader Lookup Fixture</dc:title>', `<dc:title>${title}</dc:title>`));

  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.ok()).toBeTruthy();
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token };
  const filename = `${uuid}.epub`;
  const uploaded = await page.request.post('/api/v1/upload', {
    headers,
    multipart: { file: { name: filename, mimeType: 'application/epub+zip', buffer: await zip.generateAsync({ type: 'nodebuffer' }) } },
  });
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy();
  expect((await uploaded.json()).queued).toContain(filename);

  let bookId: number | undefined;
  try {
    await expect.poll(async () => {
      const books = await page.request.get('/api/v1/books?per_page=100&sort=new');
      if (books.ok()) bookId = (await books.json()).items.find((book: { title: string }) => book.title === title)?.id;
      return bookId;
    }, { timeout: 45_000 }).toBeDefined();

    const editUrl = `/app/book/${bookId}/edit`;
    await mockSearch(page, { titleResults: [{ ...TITLE_RESULTS[1], title }] });
    await page.goto(editUrl);
    await searchFromEditor(page);
    const row = page.getByRole('listitem').filter({ hasText: 'Penguin' });
    await row.getByRole('button', { name: /choose fields/i }).click();
    await row.getByRole('button', { name: /apply selected/i }).click();
    await expect(page.getByLabel('Published', { exact: true })).toHaveValue('2021-01-01');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/app/book/${bookId}$`));
    await page.goto(editUrl);
    await expect(page.getByLabel('Published', { exact: true })).toHaveValue('2021-01-01');
  } finally {
    if (bookId !== undefined) {
      const deleted = await page.request.post(`/api/v1/books/${bookId}/delete`, { headers });
      expect(deleted.status(), 'delete the temporary publication date book').toBe(204);
    }
  }
});

test('editions view has a Close, and reopening returns to the search form (not stranded)', async ({ page }) => {
  await mockSearch(page);
  await openFetchPanel(page);
  await page.getByRole('button', { name: /^Editions$/ }).click();
  await expect(page.getByText('Back to results')).toBeVisible();

  // Close directly from the editions view, then reopen the panel.
  await page.getByRole('button', { name: /^Close$/ }).click();
  await expect(page.getByRole('button', { name: /fetch metadata from web/i })).toBeVisible();
  await page.getByRole('button', { name: /fetch metadata from web/i }).click();

  // Must land on the search form, never stranded in the editions drill-down.
  await expect(page.getByRole('button', { name: /^Search$/ })).toBeVisible();
  await expect(page.getByText('Back to results')).toHaveCount(0);
});

test('closing during a pending editions search does not strand the panel', async ({ page }) => {
  await mockSearch(page, { editionDelayMs: 1500 });
  await openFetchPanel(page);

  await page.getByRole('button', { name: /^Editions$/ }).click();
  // While the editions request is in flight, close the panel.
  await page.getByRole('button', { name: /^Close$/ }).click();
  await expect(page.getByRole('button', { name: /fetch metadata from web/i })).toBeVisible();
  // Let the abandoned response resolve, then reopen.
  await page.waitForTimeout(1800);
  await page.getByRole('button', { name: /fetch metadata from web/i }).click();

  // The late response must not have forced the panel into the editions view.
  await expect(page.getByRole('button', { name: /^Search$/ })).toBeVisible();
  await expect(page.getByText('Back to results')).toHaveCount(0);
});

test('View all details overlay shows full-length info', async ({ page }) => {
  await mockSearch(page);
  await openFetchPanel(page);

  const hcRow = page.getByRole('listitem').filter({ hasText: 'Hardcover Books' });
  await hcRow.getByRole('button', { name: 'View all details' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  // Full description (not the 140-char truncation) and identifiers one per line.
  await expect(dialog.getByText(/shows in full/)).toBeVisible();
  await expect(dialog.getByText('hardcover-id')).toBeVisible();
  await expect(dialog.getByText('the-picture-of-dorian-gray')).toBeVisible();

  // Closes on Escape.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
