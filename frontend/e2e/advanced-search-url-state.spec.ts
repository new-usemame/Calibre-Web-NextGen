import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #2211 — an advanced search has to survive the things a person does with its
 * results: open one, come back, reload to see an edit made in another tab, or
 * press Search again. Before the fix the criteria lived only in component
 * state, so each of those returned an empty form, and re-submitting unchanged
 * criteria was answered from react-query's cache without asking the server.
 *
 * Locale-independent on purpose (the seed admin's locale is not English): the
 * form is addressed by test id and position, results by their /book/ links.
 */

const TITLE_INPUT = '[data-testid=advanced-search-form] input >> nth=0';

/** A word from a real seeded title, so the search returns at least one book. */
async function seededTitleWord(page: Page): Promise<string> {
  const titles = await page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=50', { credentials: 'same-origin' });
    return ((await response.json()).items as { title: string }[]).map((b) => b.title);
  });
  // Plain ASCII keeps this spec about URL state, not about how the server
  // folds case in other scripts.
  const word = titles.flatMap((t) => t.split(/\s+/)).find((w) => /^[A-Za-z]{4,}$/.test(w));
  expect(word, `no searchable word in seeded titles ${JSON.stringify(titles.slice(0, 5))}`).toBeTruthy();
  return word!;
}

function resultLinks(page: Page) {
  return page.locator('main section a[href*="/book/"]');
}

async function search(page: Page, word: string) {
  await page.locator(TITLE_INPUT).fill(word);
  await page.locator('[data-testid=advanced-search-form] button[type=submit]').click();
  await expect(resultLinks(page).first()).toBeVisible();
}

test('the submitted criteria survive a reload and a round trip through a result', async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.goto('/app/search');
  const word = await seededTitleWord(page);
  await search(page, word);

  await expect(page).toHaveURL(new RegExp(`[?&]title=${encodeURIComponent(word)}(&|$)`));
  const shown = await resultLinks(page).count();

  await page.reload();
  await expect(page.locator(TITLE_INPUT)).toHaveValue(word);
  await expect(resultLinks(page)).toHaveCount(shown);

  await resultLinks(page).first().click();
  await expect(page).toHaveURL(/\/book\/\d+/);
  await page.goBack();
  await expect(page.locator(TITLE_INPUT)).toHaveValue(word);
  await expect(resultLinks(page)).toHaveCount(shown);

  assertNoPageErrors(errors);
});

test('pressing Search again with unchanged criteria asks the server again', async ({ page }) => {
  await page.goto('/app/search');
  const word = await seededTitleWord(page);
  let posts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/search/advanced')) posts += 1;
  });

  await search(page, word);
  await expect.poll(() => posts).toBe(1);

  await page.locator('[data-testid=advanced-search-form] button[type=submit]').click();
  await expect.poll(() => posts).toBe(2);
  await expect(resultLinks(page).first()).toBeVisible();
});
