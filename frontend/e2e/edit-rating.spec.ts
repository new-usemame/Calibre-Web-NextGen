import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Fork issue #1061 — clicking a star on the new-UI edit page did nothing on an
 * unrated book, and wiped the rating to "Not rated" on a rated one.
 *
 * Root cause was DOM semantics, not the click maths: the Field wrapper rendered
 * a <label>, and a <label> forwards any click inside it to its first labellable
 * descendant. The rating selector owns a "Clear rating" <button>, so every star
 * click produced two events — the real one on the star (which set the value)
 * and a synthesised one on the button (which immediately cleared it). Net zero.
 *
 * These specs pin the user-visible behaviour (a star click sets a rating and it
 * survives a save/reload) and the structural invariant that let the bug exist
 * (the rating widget is not inside a <label> that owns a control).
 */

const RATING = '[role="slider"][aria-label="Rating"]';

/** First book id in the library, or null on an empty seed. */
async function firstBookId(page: Page): Promise<number | null> {
  return await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { credentials: 'same-origin' });
    if (!r.ok) return null;
    const d = await r.json();
    return d.items?.[0]?.id ?? null;
  });
}

/** Click the rating widget at `frac` of its width — what a user aiming at the
 *  nth star actually does. The selector is a half-star control (#779), so a
 *  fraction maps to `ceil(frac * 10) / 2`. */
async function clickStars(page: Page, frac: number) {
  // boundingBox() is viewport-relative; measuring while the widget is below the
  // fold sends the click to the page background instead.
  await page.locator(RATING).scrollIntoViewIfNeeded();
  const box = await page.locator(RATING).boundingBox();
  expect(box, 'rating widget has no box').not.toBeNull();
  await page.mouse.click(box!.x + box!.width * frac, box!.y + box!.height / 2);
}

/** Put the widget back to "Not rated", whatever the seed left behind. The clear
 *  button is disabled at 0, so clicking it unconditionally would just hang. */
async function clearRating(page: Page) {
  const clear = page.getByRole('button', { name: /clear rating/i });
  if (await clear.isEnabled()) await clear.click();
  await expect(page.locator(RATING)).toHaveAttribute('aria-valuenow', '0');
}

// Serial: these tests share one book and mutate its rating.
test.describe.configure({ mode: 'serial' });

test.describe('#1061 star rating on the edit page', () => {
  test('clicking a star sets the rating instead of clearing it', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto('/app/');
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');

    await page.goto(`/app/book/${id}/edit`);
    const rating = page.locator(RATING);
    await expect(rating).toBeVisible();

    // Start from unrated — the reporter's first case, where nothing happened at
    // all. Pre-fix the star click was cancelled by the forwarded button click.
    await clearRating(page);

    await clickStars(page, 0.95);
    await expect(rating).toHaveAttribute('aria-valuenow', '5');

    // And from a rated book — the reporter's second case, where any star click
    // snapped the value back to "Not rated".
    await clickStars(page, 0.5);
    await expect(rating).toHaveAttribute('aria-valuenow', '2.5');
    await expect(rating).toHaveAttribute('aria-valuetext', /2\.5/);

    await clickStars(page, 0.7);
    await expect(rating).toHaveAttribute('aria-valuenow', '3.5');

    assertNoPageErrors(errors);
  });

  test('the chosen rating survives save and reload', async ({ page }) => {
    await page.goto('/app/');
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');

    await page.goto(`/app/book/${id}/edit`);
    await expect(page.locator(RATING)).toBeVisible();
    await clickStars(page, 0.75);
    await expect(page.locator(RATING)).toHaveAttribute('aria-valuenow', '4');

    await page.getByRole('button', { name: /save changes/i }).click();
    await page.waitForURL(`**/app/book/${id}`, { timeout: 15_000 });

    await page.goto(`/app/book/${id}/edit`);
    await expect(page.locator(RATING)).toHaveAttribute('aria-valuenow', '4');
  });

  test('the rating widget is not wrapped in a label that owns a control', async ({ page }) => {
    await page.goto('/app/');
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');

    await page.goto(`/app/book/${id}/edit`);
    await expect(page.locator(RATING)).toBeVisible();

    // Structural pin: re-wrapping the selector in a <label> that contains a
    // button reintroduces the implicit-activation bug, and the click assertions
    // above would only fail for reasons that read like flake. This names it.
    const enclosing = await page.evaluate((sel) => {
      const label = document.querySelector(sel)!.closest('label');
      if (!label) return { inLabel: false, controls: 0 };
      return {
        inLabel: true,
        controls: label.querySelectorAll('button, input, select, textarea').length,
      };
    }, RATING);
    expect(enclosing.inLabel && enclosing.controls > 0,
      'rating selector sits in a <label> that owns a control — clicks will be forwarded to it').toBe(false);

    // The label text must still name the group for assistive tech.
    await expect(page.getByRole('group', { name: /rating/i })).toBeVisible();
  });
});
