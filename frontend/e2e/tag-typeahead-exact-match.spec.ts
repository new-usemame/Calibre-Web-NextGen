import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Typeahead offers the exact match first, and Enter commits what you typed (#1398).
 *
 * @magdalar: typing "Romance" pre-selected "Paranormal Romance", and Enter took
 * that instead of the tag actually typed. Two causes, both user-visible here:
 *
 *   1. The suggestion query had no ordering, so an anywhere-match could sort
 *      ahead of the exact one. Reproduced on a real library — q="life" answered
 *      ["Conduct of life -- Fiction", "Life", ...].
 *   2. The menu pre-marked its first row as active, so Enter always accepted a
 *      row the user never navigated to. That also made any value sharing a
 *      substring with an existing tag impossible to type ("foo" kept becoming
 *      "Fools and Jesters").
 *
 * These belong in a browser rather than a render test: (2) is a key-composition
 * bug between the combobox and the host's Enter binding, and it looks correct in
 * source either way. The existing quick-add spec pins ArrowDown+Enter, which
 * must keep working — this pins the untouched-Enter path beside it.
 *
 * Seed-resilient: skips rather than fails when the library has no suitable tag.
 */

interface Probe {
  bookId: number | null;
  /** A tag whose name is contained in some OTHER tag's name — the shape that
   *  lets an anywhere-match outrank the exact one. */
  exact: string | null;
  /** A prefix of a real tag that is NOT itself a tag, i.e. the "foo" case. */
  novel: string | null;
}

async function probeSeed(page: Page): Promise<Probe> {
  // Navigate first — relative fetches on about:blank resolve to nothing, which
  // would skip every test while reporting green.
  if (!page.url().includes('/app')) await page.goto('/app/');
  return page.evaluate(async () => {
    const j = (u: string): Promise<any> =>
      fetch(u, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

    const names: string[] = ((await j('/api/v1/tags'))?.items ?? [])
      .map((t: any) => t?.name).filter((n: any) => typeof n === 'string');
    const lower = new Set(names.map((n) => n.toLowerCase()));

    // A book with NO tags. The field hides tags the book already carries, so on
    // a well-tagged book the candidates below are filtered out and the menu is
    // correctly empty — which reads as a failure but is really a bad seed pick.
    const books = (await j('/api/v1/books?per_page=50'))?.items ?? [];
    const bookId = books.find((b: any) => (b?.tags ?? []).length === 0)?.id ?? null;

    // The exact tag must be contained in another tag that sorts BEFORE it.
    // That is the only shape the old unordered query actually got wrong, so
    // anything looser makes this test pass with or without the fix:
    // "Conduct of life -- Fiction" sorts ahead of "Life" and hid it, while
    // "Fantasy fiction" sorts after "Fantasy" and never did.
    const exact = names.find((n) => n.length >= 3 && names.some((other) => {
      const o = other.toLowerCase();
      const needle = n.toLowerCase();
      return other !== n && o.includes(needle) && o < needle;
    })) ?? null;

    // A 3-char prefix that is not itself a tag — the "foo" / "Fools and
    // Jesters" case. Its source tag keeps the menu open when Enter is pressed,
    // which is the race the reporter actually hit.
    const source = names.find((n) => n.length >= 5 && !lower.has(n.slice(0, 3).toLowerCase()));
    const novel = source ? source.slice(0, 3) : null;

    return { bookId, exact, novel };
  });
}

async function openQuickAdd(page: Page, bookId: number) {
  await page.goto(`/app/book/${bookId}`);
  const addButton = page.getByRole('button', { name: /add tag/i }).last();
  await addButton.waitFor({ state: 'visible' });
  await addButton.click();
  return page.getByRole('combobox', { name: /add tag/i });
}

test.describe('typeahead exact-match ranking and Enter (#1398)', () => {
  test('the exact match is offered first, ahead of longer tags containing it', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId || !seed.exact, 'seed has no tag contained in another tag');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill(seed.exact!);

    const options = page.getByRole('listbox').getByRole('option');
    await expect(options.first()).toBeVisible();
    // Pre-fix this was whichever anywhere-match the name index happened to
    // yield first, which is the tag the user then got by pressing Enter.
    await expect(options.first()).toHaveText(seed.exact!);
  });

  test('no suggestion is pre-selected before the user navigates', async ({ page }) => {
    const seed = await probeSeed(page);
    test.skip(!seed.bookId || !seed.exact, 'seed has no suitable tag');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill(seed.exact!);
    await expect(page.getByRole('listbox').getByRole('option').first()).toBeVisible();

    // The mechanism behind the reported Enter bug: an active option means the
    // combobox owns Enter, so the host's "add what I typed" never runs.
    await expect(field).not.toHaveAttribute('aria-activedescendant', /./);
    await expect(page.getByRole('listbox').getByRole('option', { selected: true })).toHaveCount(0);

    // ArrowDown still activates the first option — the existing quick-add spec
    // depends on that path and users still need it.
    await field.press('ArrowDown');
    await expect(field).toHaveAttribute('aria-activedescendant', /./);
  });

  test('Enter adds exactly what was typed, even when it is inside an existing tag', async ({ page }) => {
    const errors = collectPageErrors(page);
    const seed = await probeSeed(page);
    test.skip(!seed.bookId || !seed.novel, 'seed has no tag prefix that is not itself a tag');

    const field = await openQuickAdd(page, seed.bookId!);
    await field.fill(seed.novel!);
    // Wait for the menu, so this exercises the race the reporter hit: pre-fix,
    // Enter after the fetch landed took the suggestion instead of the text.
    await expect(page.getByRole('listbox').getByRole('option').first()).toBeVisible();
    await field.press('Enter');

    const added = page.getByRole('button', { name: `Remove tag ${seed.novel!}` });
    await expect(added).toBeVisible({ timeout: 10_000 });

    // Leave the library as we found it.
    await added.click();
    await expect(added).toHaveCount(0, { timeout: 10_000 });

    assertNoPageErrors(errors);
  });
});
