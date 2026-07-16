import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #928 — a saved default library view must still render THE LIBRARY.
 *
 * #891 shipped #498 by swapping the whole page component: `/` rendered
 * <AdvancedSearch> instead of <Catalog> whenever a default filter existed, so
 * setting one turned the library home into the search page — search form on top,
 * "Advanced search" title, no library heading/actions, no Discover strip.
 *
 * The filter is a VIEW OF THE LIBRARY, not a different page. These specs pin the
 * library chrome AND that the filter is genuinely applied, so a future refactor
 * can't satisfy one by dropping the other.
 *
 * Locale independence is deliberate: the harness seed's admin has locale=ru, and
 * an assertion on English copy passes only on the brief pre-i18n render. Each
 * spec compares the filtered page against the SAME page unfiltered, or matches
 * digits — never a translated string.
 */

test.describe.configure({ mode: 'serial' });

/*
 * Runs in the desktop project only — see the `testIgnore` note in
 * playwright.config.ts. The default view is ACCOUNT state and every project
 * shares one seed login, so running this concurrently in two projects has each
 * one's writes (and the afterEach reset) clobber the other's.
 */

/** Persist a default library filter the way the Advanced-search page does. */
async function setDefaultFilter(page: Page, value: unknown) {
  const status = await page.evaluate(async (filter) => {
    const csrf = await (await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })).json();
    const response = await fetch('/ajax/view', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
      body: JSON.stringify({ catalog: { default_filter: filter } }),
    });
    return response.status;
  }, value);
  expect(status).toBe(200);
}

/** The count strip renders localized ("35 books" / "Книг: 35"); the number is the assertion. */
async function shownCount(page: Page): Promise<number> {
  const text = await page.getByTestId('catalog-count').textContent();
  const digits = (text ?? '').match(/\d+/);
  expect(digits, `no count in "${text}"`).not.toBeNull();
  return Number(digits![0]);
}

/** Distinct books rendered in the library grid. Counting `a[href*="/book/"]`
 *  would be wrong twice over: each card carries two links (cover + title), and
 *  the Discover strip contributes its own picks. */
async function gridBookCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid=catalog-page]');
    if (!root) return -1;
    const ids = new Set<string>();
    for (const link of root.querySelectorAll('a[href*="/book/"]')) {
      if (link.closest('[data-testid=discover-section]')) continue;
      const id = link.getAttribute('href')?.match(/\/book\/(\d+)/)?.[1];
      if (id) ids.add(id);
    }
    return ids.size;
  });
}

async function libraryTotal(page: Page): Promise<number> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', { credentials: 'same-origin' });
    return (await response.json()).total as number;
  });
}

/** Live ground truth for "how many books does this tag select right now". */
async function advancedTotal(page: Page, tagId: number): Promise<number> {
  return page.evaluate(async (id) => {
    const csrf = await (await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })).json();
    const response = await fetch('/api/v1/search/advanced', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
      body: JSON.stringify({ include_tag: [id], per_page: 1 }),
    });
    return (await response.json()).total as number;
  }, tagId);
}

/** A tag that selects a real, PROPER subset of the library — so "the filter was
 *  applied" and "the filter was ignored" can't produce the same count. */
async function subsetTag(page: Page): Promise<{ id: number; total: number } | null> {
  return page.evaluate(async () => {
    const csrf = await (await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })).json();
    const library = await fetch('/api/v1/books?per_page=1', { credentials: 'same-origin' });
    const libraryTotal = (await library.json()).total as number;

    const tags = await fetch('/api/v1/tags?per_page=25', { credentials: 'same-origin' });
    if (!tags.ok) return null;
    for (const tag of (await tags.json()).items ?? []) {
      const response = await fetch('/api/v1/search/advanced', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
        body: JSON.stringify({ include_tag: [tag.id], per_page: 1 }),
      });
      if (!response.ok) continue;
      const total = (await response.json()).total as number;
      if (total > 0 && total < libraryTotal) return { id: tag.id, total };
    }
    return null;
  });
}

test.afterEach(async ({ page }) => {
  // Never leave a default filter behind — it would silently reshape every other spec.
  await page.goto('/app');
  await setDefaultFilter(page, null);
});

test('a saved default view keeps the library page (not the search page)', async ({ page }) => {
  await page.goto('/app');
  const errors = collectPageErrors(page);
  await setDefaultFilter(page, null);
  await page.reload();

  // Control: the library as it looks with no default view. Captured rather than
  // hardcoded so this holds in any UI language.
  await expect(page.getByTestId('catalog-page')).toBeVisible();
  const libraryHeading = await page.locator('h1').textContent();
  const libraryTitle = await page.title();
  expect(libraryHeading?.trim()).toBeTruthy();

  await setDefaultFilter(page, { read_status: 'unread' });
  await page.goto('/app');

  // Still the library: same page component, same heading, same document title.
  await expect(page.getByTestId('catalog-page')).toBeVisible();
  await expect(page.locator('h1')).toHaveText(libraryHeading!.trim());
  await expect(page).toHaveTitle(libraryTitle);

  // Not the search page: its form must not be pinned to the library home.
  await expect(page.getByTestId('advanced-search-form')).toHaveCount(0);

  // Library actions + the Discover strip are the rest of symptom 2.
  await expect(page.getByTestId('catalog-view-settings')).toBeVisible();
  await expect(page.getByTestId('discover-section')).toBeVisible();

  assertNoPageErrors(errors);
});

test('the saved filter is actually applied to the library listing', async ({ page }) => {
  await page.goto('/app');
  const tag = await subsetTag(page);
  // A skip here would be a silent coverage hole, not a pass: the seed is the
  // project's own fixture and it HAS multi-book tags. Fail loudly instead.
  expect(tag, 'seed exposes no tag selecting a proper subset').not.toBeNull();

  await setDefaultFilter(page, { include_tag: [tag!.id] });
  await page.goto('/app');

  // The count reflects the FILTERED set, not the whole library — and the cards
  // agree with the count, so "applied" can't be faked by the header alone.
  // Re-read the expected total at assert time and poll: sibling specs share this
  // login and mutate the library (hidden-books hides a book), so a total captured
  // earlier can go stale mid-test. Polling against live ground truth keeps this
  // a real assertion instead of a race.
  await expect(page.getByTestId('catalog-count')).toBeVisible();
  await expect.poll(async () => {
    const expected = await advancedTotal(page, tag!.id);
    return await shownCount(page) === expected && await gridBookCount(page) === expected;
  }, { message: 'library count/cards never matched the filtered total' }).toBe(true);
});

test('a default view offers a way back to the whole library', async ({ page }) => {
  await page.goto('/app');
  const errors = collectPageErrors(page);
  const total = await libraryTotal(page);
  // A proven proper subset, so "Show all" demonstrably changes the listing —
  // a seed where every book is unread would make that assertion vacuous.
  const tag = await subsetTag(page);
  expect(tag, 'seed exposes no tag selecting a proper subset').not.toBeNull();
  await setDefaultFilter(page, { include_tag: [tag!.id] });
  await page.goto('/app');

  // A filter that silently hides books with no escape is a trap: the user must be
  // told the view is filtered and be able to see everything.
  await expect(page.getByTestId('default-filter-notice')).toBeVisible();
  const showAll = page.getByTestId('default-filter-show-all');
  await expect(showAll).toBeVisible();

  const filteredCount = await shownCount(page);
  expect(filteredCount).toBeLessThan(total);

  await showAll.click();

  // Assert RELATIVELY (more books than the filter showed) rather than against a
  // total captured earlier: sibling specs share this login and can hide a book
  // mid-test, which would move an absolute target and fail a working feature.
  await expect(page.getByTestId('default-filter-show-all')).toHaveCount(0);
  await expect(page.getByTestId('default-filter-notice')).toHaveCount(0);
  await expect.poll(() => shownCount(page),
    { message: 'Show all did not widen the listing beyond the filtered set' })
    .toBeGreaterThan(filteredCount);

  assertNoPageErrors(errors);
});
