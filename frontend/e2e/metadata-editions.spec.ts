import { test, expect } from '@playwright/test';
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
const TITLE_RESULTS = [
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
    publishedDate: '1949-01-01',
    source: { id: 'openlibrary', description: 'Open Library', link: 'https://openlibrary.org' },
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
    publishedDate: '2003-02-04',
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

async function mockSearch(page: import('@playwright/test').Page, opts: { editionDelayMs?: number } = {}) {
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
        results: isEditionQuery ? EDITION_RESULTS : TITLE_RESULTS,
        providers: [{ id: 'hardcover', name: 'Hardcover', status: 'ok', count: 2, message: '' }],
      }),
    });
  });
}

/** Open the edit page of the first book in the grid and reveal the fetch panel. */
async function openFetchPanel(page: import('@playwright/test').Page) {
  await page.goto('/app');
  const firstBook = page.locator('a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible();
  const href = await firstBook.getAttribute('href');
  const id = (href || '').match(/\/book\/(\d+)/)?.[1];
  expect(id, 'could not resolve a book id from the grid').toBeTruthy();

  await page.goto(`/app/book/${id}/edit`);
  await page.getByRole('button', { name: /fetch metadata from web/i }).click();
  await page.getByRole('button', { name: /^Search$/ }).click();
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
  await paperback.getByRole('button', { name: /apply selected/i }).click();

  // The chosen edition's identifiers now populate the form's identifiers table.
  const idents = await page.evaluate(() => {
    const types = [...document.querySelectorAll('input[aria-label="Identifier type"]')].map((i) => (i as HTMLInputElement).value);
    const vals = [...document.querySelectorAll('input[aria-label="Identifier value"]')].map((i) => (i as HTMLInputElement).value);
    return types.map((t, i) => ({ type: t, val: vals[i] }));
  });
  expect(idents).toContainEqual({ type: 'hardcover-edition', val: '32701290' });
  expect(idents).toContainEqual({ type: 'isbn', val: '9789815204230' });
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
