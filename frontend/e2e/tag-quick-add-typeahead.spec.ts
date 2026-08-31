import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Quick-add tag field on the book page suggests existing tags (#741, #572).
 *
 * The full editor got typeahead in #741, but the inline quick-add on the book
 * page stayed a bare <input>. Two reporters came back on separate threads to
 * say so — @magdalar ("the quick-edit of tags on the Book page doesn't seem to
 * auto-complete/type-ahead") and, on #572, the same wish "especially to avoid
 * typos", which is the whole point: without suggestions the field mints
 * near-duplicate tags from misspellings and nobody notices until the tag list
 * is full of them.
 *
 * These drive the real component against a real library, because the failure
 * this guards is one a render test cannot see: the combobox has to fetch from
 * the live endpoint, and Enter/Escape have to stay bound to commit/cancel while
 * the arrow keys still walk the suggestion list. Getting the key composition
 * wrong is the likely regression, and it looks perfectly fine in source.
 *
 * Seed-resilient: skips rather than fails when the library has no editable book
 * or no tags to suggest.
 */

interface Probe {
  bookId: number | null;
  tagName: string | null;
  prefix: string | null;
}

async function probeSeed(page: Page): Promise<Probe> {
  // Navigate FIRST. The probe fetches relative URLs, and on about:blank they
  // resolve to nothing, every probe comes back null, and every test below
  // skips — a run that reports green while asserting nothing at all.
  if (!page.url().includes('/app')) await page.goto('/app/');
  return page.evaluate(async () => {
    const j = (u: string): Promise<any> =>
      fetch(u, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

    const names: string[] = ((await j('/api/v1/tags'))?.items ?? [])
      .map((t: any) => t?.name).filter((n: any) => typeof n === 'string');
    const lower = new Set(names.map((n) => n.toLowerCase()));
    // Two constraints the earlier probe missed, both of which made these specs
    // fail on a seed where the first book happens to be well-tagged:
    //  - the field hides tags the book ALREADY has, so a candidate already on
    //    that book yields an empty menu and no listbox to assert on. Picking an
    //    untagged book removes the whole class.
    //  - a prefix that is itself a tag now ranks first (#1398), so accepting
    //    the top suggestion would legitimately leave the field unchanged and
    //    the "suggestion is longer than what I typed" assertions would fail.
    const books = (await j('/api/v1/books?per_page=50'))?.items ?? [];
    const bookId = books.find((b: any) => (b?.tags ?? []).length === 0)?.id ?? null;

    const tag = names.find((n) => n.length >= 4 && !lower.has(n.slice(0, 3).toLowerCase()));

    return {
      bookId,
      tagName: tag ?? null,
      prefix: tag ? tag.slice(0, 3) : null,
    };
  });
}

async function openQuickAdd(page: Page, bookId: number) {
  await page.goto(`/app/book/${bookId}`);
  const addButton = page.getByRole('button', { name: /add tag/i }).last();
  await addButton.waitFor({ state: 'visible' });
  await addButton.click();
  return page.getByRole('combobox', { name: /add tag/i });
}

test.describe('quick-add tag typeahead (#741)', () => {
  test('the quick-add field is a combobox, not a bare text input', async ({ page }) => {
    const errors = collectPageErrors(page);
    const seed = await probeSeed(page);
    test.skip(!seed.bookId, 'seed has no books');

    const field = await openQuickAdd(page, seed.bookId!);

    // role=combobox is the observable difference between the fixed field and
    // the bare <input> both reporters were looking at.
    await expect(field).toBeVisible();
    await expect(field).toHaveAttribute('aria-autocomplete', 'list');

    assertNoPageErrors(errors);
  });

  test('typing a prefix offers matching existing tags', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId || !seed.prefix, 'seed has no books or no tags to suggest');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill(seed.prefix!);

    const listbox = page.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await expect(listbox.getByRole('option').first()).toBeVisible();
  });

  test('arrow keys walk the suggestions and Enter accepts the highlighted one', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId || !seed.prefix, 'seed has no books or no tags to suggest');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill(seed.prefix!);
    await expect(page.getByRole('listbox')).toBeVisible();

    const first = await page.getByRole('listbox').getByRole('option').first().textContent();
    await field.press('ArrowDown');
    await field.press('Enter');

    // Enter with the menu open belongs to the combobox: it accepts the
    // suggestion into the field. It must NOT fall through and commit the raw
    // typed prefix as a brand-new tag — that would be the typo-minting
    // behaviour this whole change exists to stop.
    await expect(field).not.toHaveValue(seed.prefix!);
    expect((await field.inputValue()).length).toBeGreaterThan(seed.prefix!.length);
    if (first) expect(first).toContain(seed.prefix!);
  });

  test('Escape cancels the quick-add without committing anything', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId, 'seed has no books');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill('zzz-should-never-be-created');
    // First Escape closes the suggestion menu if it opened; the field itself
    // must still be dismissible, which is the host's Escape binding surviving
    // the combobox's.
    await field.press('Escape');
    await field.press('Escape');

    await expect(page.getByRole('combobox', { name: /add tag/i })).toHaveCount(0);
    await expect(page.getByText('zzz-should-never-be-created')).toHaveCount(0);
  });

  test('tags already on the book are not offered again', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId, 'seed has no books');

    // Find a book that actually has a tag, so there is something to exclude.
    const withTag = await page.evaluate(async () => {
      const r = await fetch('/api/v1/books?per_page=25', { headers: { Accept: 'application/json' } })
        .then((x) => (x.ok ? x.json() : null)).catch(() => null);
      for (const b of r?.items ?? []) {
        const d = await fetch(`/api/v1/books/${b.id}`, { headers: { Accept: 'application/json' } })
          .then((x) => (x.ok ? x.json() : null)).catch(() => null);
        const tags = d?.tags ?? [];
        if (tags.length) return { id: b.id, tag: tags[0].name as string };
      }
      return null;
    });
    test.skip(!withTag, 'no book in the seed carries a tag');

    const field = await openQuickAdd(page, withTag!.id);
    await field.fill(withTag!.tag.slice(0, 3));

    const listbox = page.getByRole('listbox');
    if (await listbox.count()) {
      // multi={false} parses no tokens out of the field, so without the
      // host-supplied exclusions the book's own tag would be offered — and
      // picking it would be a silent no-op on commit.
      await expect(listbox.getByRole('option', { name: withTag!.tag, exact: true })).toHaveCount(0);
    }
  });
});
