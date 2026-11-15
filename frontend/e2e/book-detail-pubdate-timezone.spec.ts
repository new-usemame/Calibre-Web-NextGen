import { test, expect, type Page } from '@playwright/test';
import type { BookDetail } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Fork issue #2634, reported by @gueland: save a book with the right
 * publication date and the book page shows the day before. The date is a
 * calendar day ("2021-03-15"); the page read it as UTC midnight and printed it
 * in the browser's zone, so every reader west of UTC lost a day. The browser
 * here runs in Los Angeles to reproduce that.
 */
test.use({ timezoneId: 'America/Los_Angeles' });

const BOOK_ID = 9002;

function detail(pubdate: string): BookDetail {
  return {
    id: BOOK_ID,
    title: 'Mock book with a publication date',
    authors: [{ id: 1, name: 'Mock Author' }],
    series: null,
    series_index: '',
    rating: null,
    cover_url: null,
    pubdate,
    date_added: null,
    last_modified: null,
    description_html: null,
    original_filename: null,
    tags: [],
    languages: [],
    publishers: [],
    identifiers: [],
    formats: [],
    read: false,
    archived: false,
    favorited: false,
    hidden: false,
    in_progress: false,
    kosync_progress: null,
    kosync_progress_timestamp: null,
    kosync_progress_created_at: null,
  };
}

async function mockBook(page: Page, pubdate: string) {
  await page.route(`**/api/v1/books/${BOOK_ID}/review`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ review: null }) })
      : route.continue());
  await page.route(`**/api/v1/books/${BOOK_ID}/rating`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ personal_rating: null, household_rating: null }),
      })
      : route.continue());
  await page.route(`**/api/v1/books/${BOOK_ID}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail(pubdate)) })
      : route.continue());
}

function publishedValue(page: Page) {
  return page.locator('dt', { hasText: /^Published$/ }).locator('xpath=following-sibling::dd[1]');
}

test('the book page shows the publication date that was saved (#2634)', async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBook(page, '2021-03-15');
  await page.goto(`/app/book/${BOOK_ID}`);
  await expect(publishedValue(page)).toHaveText('March 15, 2021');
  assertNoPageErrors(errors);
});

test('a year-only publication date shows as its year, not the year before (#2634)', async ({ page }) => {
  await mockBook(page, '2020-01-01');
  await page.goto(`/app/book/${BOOK_ID}`);
  await expect(publishedValue(page)).toHaveText('2020');
});
