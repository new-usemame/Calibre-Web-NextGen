import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors, assertNoHorizontalOverflow } from './utils';

/*
 * Book-detail completeness pass:
 *   - Star rating (parity with the classic detail page) — the serializer now
 *     emits `rating` (0–10) and the page renders it as five stars.
 *   - "More by this author" strip — fills the page for sparse/description-less
 *     books and turns the detail page into a browse surface.
 *   - i18n regression guard — the SPA interpolates {brace} placeholders, NOT
 *     gettext %(name)s; a %()s msgid renders its placeholder literally. This
 *     shipped a literal "More by %(name)s" in dev before the fix, and the same
 *     class of bug was live in BulkBar. The heading below must never contain a
 *     raw placeholder.
 *
 * Seed-resilient: the specs query the API for a rated book / a multi-book author
 * and skip (not fail) when the seed lacks one, so they stay green on any library.
 */

interface DetailProbe {
  ratedBookId: number | null;
  ratedValue: number | null;
  multiAuthorBookId: number | null;   // a book whose author has >1 title
}

async function probeSeed(page: Page): Promise<DetailProbe> {
  // A handful of light entity-browse requests — no per-book detail fan-out,
  // which would backlog the single-worker dev server and stall the run.
  return page.evaluate(async () => {
    const j = (u: string): Promise<any> =>
      fetch(u, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

    // A rated book via the ratings-bucket browse, then its exact 0–10 value.
    let ratedBookId: number | null = null;
    let ratedValue: number | null = null;
    const rid = (await j('/api/v1/ratings'))?.items?.[0]?.id;
    if (rid != null) {
      ratedBookId = (await j(`/api/v1/books?rating=${rid}&per_page=1`))?.items?.[0]?.id ?? null;
      if (ratedBookId != null) ratedValue = (await j(`/api/v1/books/${ratedBookId}`))?.rating ?? null;
    }

    // A book whose author has more than one title, via the authors browse.
    let multiAuthorBookId: number | null = null;
    const multi = ((await j('/api/v1/authors'))?.items ?? []).find(
      (a: { count: number }) => a.count > 1,
    );
    if (multi) {
      multiAuthorBookId = (await j(`/api/v1/books?author=${multi.id}&per_page=1`))?.items?.[0]?.id ?? null;
    }

    return { ratedBookId, ratedValue, multiAuthorBookId };
  });
}

test('a rated book shows a star rating with an accurate out-of-5 label', async ({ page }) => {
  await page.goto('/app');
  const seed = await probeSeed(page);
  test.skip(seed.ratedBookId == null, 'seed has no rated book');

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${seed.ratedBookId}`, { waitUntil: 'domcontentloaded' });

  const rating = page.locator('[role="img"][aria-label^="Rated"]');
  await expect(rating).toBeVisible({ timeout: 10_000 });

  // Label reflects the value: 0–10 Calibre rating → 0–5 stars (value / 2).
  const expectedStars = (seed.ratedValue! / 2).toString();
  await expect(rating).toHaveAttribute('aria-label', new RegExp(`Rated ${expectedStars} out of 5`));
  // Exactly five star slots are rendered.
  await expect(rating.locator('> span')).toHaveCount(5);

  assertNoPageErrors(errors);
});

test('"More by this author" strip renders with a fully-interpolated heading (no raw placeholder)', async ({ page }) => {
  await page.goto('/app');
  const seed = await probeSeed(page);
  test.skip(seed.multiAuthorBookId == null, 'seed has no author with multiple books');

  const errors = collectPageErrors(page);
  await page.goto(`/app/book/${seed.multiAuthorBookId}`, { waitUntil: 'domcontentloaded' });

  const strip = page.locator('section[aria-label^="More by"]');
  await expect(strip).toBeVisible({ timeout: 10_000 });

  // The heading must be interpolated — never the literal msgid placeholder.
  const heading = strip.locator('h2');
  await expect(heading).not.toContainText('{name}');
  await expect(heading).not.toContainText('%(name)s');
  // Strip has at least one sibling book, and never the book we're viewing.
  await expect(strip.locator('a[href*="/book/"]').first()).toBeVisible();
  await expect(strip.locator(`a[href$="/book/${seed.multiAuthorBookId}"]`)).toHaveCount(0);

  assertNoPageErrors(errors);
});

test('book detail with a "More by" strip has no horizontal overflow on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');
  const seed = await probeSeed(page);
  test.skip(seed.multiAuthorBookId == null, 'seed has no author with multiple books');

  await page.goto(`/app/book/${seed.multiAuthorBookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('section[aria-label^="More by"]')).toBeVisible({ timeout: 10_000 });
  await assertNoHorizontalOverflow(page);
});
