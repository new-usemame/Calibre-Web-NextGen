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

/** First book id in the seeded library, or null. */
async function firstBookId(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null))
      .catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
}

// #803 — the new UI had no way to delete a book (users had to switch to classic).
// The book-detail page now carries a whole-book delete action, gated on the
// delete role and confirmed before it fires. These fail pre-fix (no button).

test('a permitted user gets a delete action that confirms, calls the delete endpoint, and returns to the library (#803)', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  const errors = collectPageErrors(page);

  // Stub the destructive call so the shared seed library isn't actually mutated,
  // while still proving the button fires the correct endpoint.
  await page.route(`**/api/v1/books/${bookId}/delete`, async (route) => {
    await route.fulfill({ status: 204, contentType: 'application/json', body: '' });
  });
  // A confirm() must be accepted for the delete to proceed.
  let sawConfirm = false;
  page.on('dialog', (d) => {
    sawConfirm = d.type() === 'confirm';
    void d.accept();
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });

  const del = page.getByRole('button', { name: 'Delete book' });
  await expect(del).toBeVisible({ timeout: 10_000 });

  // Clicking fires the confirm dialog, then a POST to the whole-book delete
  // endpoint (not a per-format one). Capture the request so the assertion on
  // it isn't racy against the route handler.
  const [req] = await Promise.all([
    page.waitForRequest(`**/api/v1/books/${bookId}/delete`, { timeout: 10_000 }),
    del.click(),
  ]);
  expect(sawConfirm, 'a confirm dialog was shown before deleting').toBe(true);
  expect(req.method()).toBe('POST');

  // After success the user leaves the (now-deleted) book's detail page and
  // lands back in the library — never stranded on a dead /book/<id> route.
  await expect(page).not.toHaveURL(new RegExp(`/book/${bookId}\\b`), { timeout: 10_000 });

  assertNoPageErrors(errors);
});

test('the delete action is hidden for a user without the delete role (#803)', async ({ page }) => {
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  // Force the current-user payload to lack the delete role; the control must
  // not render at all (hidden, never merely disabled — a forged request is
  // separately rejected server-side with 403).
  await page.route('**/api/v1/auth/me', async (route) => {
    const res = await route.fetch();
    const me = await res.json();
    if (me?.role) me.role.delete_books = false;
    await route.fulfill({ response: res, json: me });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  // The page has rendered (an existing action is present) but delete is absent.
  await expect(page.getByRole('button', { name: /Mark as (read|unread)/ })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole('button', { name: 'Delete book' })).toHaveCount(0);
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

// A reader without the edit role sees the read-only tag pills, and those pills
// were `white-space: nowrap` with no max-width. Real libraries carry
// Library-of-Congress subject headings ("France -- History -- Revolution,
// 1789-1799 -- Fiction") that are wider than a phone, so one tag dragged the
// whole detail page into horizontal scroll. The admin-role branch renders
// removable chips instead and wraps fine, which is why the existing mobile
// specs — all of which log in as admin — never saw this.
//
// Deterministic on any seed: the role and the tag list are both stubbed.
test('long tag names do not push the read-only detail page into horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');
  const bookId = await firstBookId(page);
  test.skip(bookId == null, 'seed has no books');

  // Drop the edit role so the page renders the read-only Pill branch that a
  // guest or viewer account gets.
  await page.route('**/api/v1/auth/me', async (route) => {
    const res = await route.fetch();
    const me = await res.json();
    if (me?.role) me.role.edit = false;
    await route.fulfill({ response: res, json: me });
  });

  // A real LoC heading plus a single unbroken token wider than the viewport.
  await page.route(`**/api/v1/books/${bookId}`, async (route) => {
    const res = await route.fetch();
    const book = await res.json();
    book.tags = [
      { id: 990001, name: 'France -- History -- Revolution, 1789-1799 -- Fiction' },
      { id: 990002, name: 'Bildungsroman'.repeat(8) },
    ];
    await route.fulfill({ response: res, json: book });
  });

  await page.goto(`/app/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#book-tags')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('France -- History -- Revolution, 1789-1799 -- Fiction')).toBeVisible();
  await assertNoHorizontalOverflow(page);
});
