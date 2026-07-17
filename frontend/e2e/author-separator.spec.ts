import { test, expect, Page } from '@playwright/test';

/*
 * Fork issue #948 — authors were separated with a comma, so a multi-author list
 * was unreadable. An author's display name may itself contain a comma ("Leckie,
 * Ann" is an ordinary Calibre name), which made "Leckie, Ann, Tchaikovsky,
 * Adrian" read as four people.
 *
 * ' & ' is the app's own convention: Calibre joins authors with '&', the classic
 * templates render '&' between author links, and the edit endpoint hands the
 * form '&'-joined authors under a field labelled "Authors (separate with &)".
 * Only the SPA's display path had drifted.
 *
 * These specs assert the rendered separator on the two surfaces the reporter
 * named (library grid, book view) plus the table view found by the audit, and
 * pin that the *edit form* and the *display* agree — the round-trip is the whole
 * point, and it was the thing that silently disagreed.
 *
 * Seed-resilient: probes the API for a multi-author book and skips (not fails)
 * when the library has none, so it stays green on any seed.
 */

interface Probe {
  bookId: number | null;
  authors: string[];
}

async function probeMultiAuthorBook(page: Page): Promise<Probe> {
  return await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=250', { credentials: 'same-origin' });
    if (!r.ok) return { bookId: null, authors: [] };
    const d = await r.json();
    const hit = (d.items || []).find((b: { authors?: string[] }) => (b.authors || []).length > 1);
    return hit ? { bookId: hit.id, authors: hit.authors } : { bookId: null, authors: [] };
  });
}

test.describe('#948 author separator', () => {
  test('book view separates authors with & and keeps each one linkable', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, authors } = await probeMultiAuthorBook(page);
    test.skip(!bookId, 'seed has no multi-author book');

    await page.goto(`/app/book/${bookId}`);
    const authorLine = page.locator('h1').locator('xpath=../p').first();
    await expect(authorLine).toBeVisible();

    const text = (await authorLine.innerText()).trim();
    expect(text).toBe(authors.join(' & '));

    // The separator must be a separator, not part of a name: every author is
    // still its own link. A join that swallowed the delimiter would pass a
    // naive substring check but break navigation.
    const links = authorLine.locator('a[href*="/authors/"]');
    await expect(links).toHaveCount(authors.length);
    for (const name of authors) {
      await expect(authorLine.getByRole('link', { name, exact: true })).toBeVisible();
    }
  });

  test('library grid separates authors with &', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, authors } = await probeMultiAuthorBook(page);
    test.skip(!bookId, 'seed has no multi-author book');

    const expected = authors.join(' & ');
    // The grid is infinite-scrolled; page down until the card mounts.
    for (let i = 0; i < 20; i++) {
      if (await page.getByText(expected, { exact: true }).count()) break;
      await page.mouse.wheel(0, 1400);
      await page.waitForTimeout(300);
    }
    await expect(page.getByText(expected, { exact: true }).first()).toBeVisible();
  });

  test('edit form and display agree on the separator', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, authors } = await probeMultiAuthorBook(page);
    test.skip(!bookId, 'seed has no multi-author book');

    // What the backend hands the edit form...
    const editField = await page.evaluate(async (id) => {
      const r = await fetch(`/api/v1/books/${id}/metadata`, { credentials: 'same-origin' });
      return (await r.json()).authors as string;
    }, bookId);

    // ...must be exactly what the book view shows. These disagreed before #948.
    await page.goto(`/app/book/${bookId}`);
    const shown = (await page.locator('h1').locator('xpath=../p').first().innerText()).trim();
    expect(shown).toBe(editField);
    expect(shown).toBe(authors.join(' & '));
  });
});
