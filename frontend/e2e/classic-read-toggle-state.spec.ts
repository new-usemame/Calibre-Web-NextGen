import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/**
 * #771 — the Classic cover-card checkbox and detail checkbox must describe
 * the same current read state. The card control is injected by caliBlur.js,
 * so a template/source assertion cannot exercise the regression that escaped
 * #811; this test drives the rendered browser DOM and the real toggle route.
 */

test('Classic catalog and detail checkboxes stay state-consistent before and after a toggle (#771)', async ({ page, isMobile }) => {
  test.skip(isMobile === true, 'Classic cover quick-actions are desktop-hover controls');

  await page.goto('/app');
  const bookId = await page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    return (await response.json())?.items?.[0]?.id ?? null;
  });
  test.skip(bookId == null, 'seed has no books');

  // The shared e2e setup intentionally opens the SPA. Enter the real transient
  // Classic fallback before testing its direct routes.
  await page.goto('/?cwng_feedback=newui', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => new URL(page.url()).pathname).toBe('/');

  const errors = collectPageErrors(page);
  await page.goto(`/book/${bookId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveClass(/\bblur\b/);

  const detailIcon = page.locator('#read-icon');
  await expect(detailIcon).toBeVisible();
  const initiallyRead = await detailIcon.evaluate((icon) => icon.classList.contains('glyphicon-check'));
  const initialAction = initiallyRead ? 'Mark As Unread' : 'Mark As Read';
  await expect(page.locator('#toggle-read-btn')).toHaveAttribute('aria-label', initialAction);

  const listPath = initiallyRead ? '/read/stored/' : '/unread/stored/';
  await page.goto(listPath, { waitUntil: 'domcontentloaded' });
  // Some Classic shelves render a hidden fallback carousel alongside the
  // visible list. Target the actual interactive card, not its hidden twin.
  const card = page.locator(`a.book-cover-link[data-book-id="${bookId}"]:visible`).first();
  await expect(card).toBeVisible();
  const cardButton = card.locator('.read-toggle-btn');
  const cardIcon = cardButton.locator('.glyphicon');
  const readBadge = card.locator('.badge.read, .cover-badge-read');
  await expect(cardButton).toBeVisible();

  // Load-state contract: checked means read and empty means unread on both
  // surfaces; the tooltip separately names the action clicking will perform.
  await expect(cardIcon).toHaveClass(initiallyRead ? /\bglyphicon-check\b/ : /\bglyphicon-unchecked\b/);
  await expect(cardButton).toHaveAttribute('title', initialAction);
  await expect(readBadge).toHaveCount(initiallyRead ? 1 : 0);

  let restored = true;
  try {
    const toggleResponse = page.waitForResponse(
      (response) => response.url().includes(`/ajax/toggleread/${bookId}`) && response.request().method() === 'POST',
    );
    await cardButton.click();
    expect((await toggleResponse).ok()).toBeTruthy();
    restored = false;

    // caliBlur briefly shows a success glyph, then settles on the state glyph.
    await expect(cardIcon).toHaveClass(initiallyRead ? /\bglyphicon-unchecked\b/ : /\bglyphicon-check\b/, {
      timeout: 3_000,
    });
    await expect(cardButton).toHaveAttribute('title', initiallyRead ? 'Mark As Read' : 'Mark As Unread');
    await expect(readBadge).toHaveCount(initiallyRead ? 0 : 1);

    // The separate detail code path must render the same new state.
    await page.goto(`/book/${bookId}`, { waitUntil: 'domcontentloaded' });
    await expect(detailIcon).toHaveClass(initiallyRead ? /\bglyphicon-unchecked\b/ : /\bglyphicon-check\b/);

    // Restore the shared seed through the real detail toggle route.
    const restoreResponse = page.waitForResponse(
      (response) => response.url().includes(`/ajax/toggleread/${bookId}`) && response.request().method() === 'POST',
    );
    await page.locator('#toggle-read-btn').click();
    expect((await restoreResponse).ok()).toBeTruthy();
    await expect(detailIcon).toHaveClass(initiallyRead ? /\bglyphicon-check\b/ : /\bglyphicon-unchecked\b/);
    restored = true;
  } finally {
    if (!restored) {
      await page.goto(`/book/${bookId}`, { waitUntil: 'domcontentloaded' });
      const currentRead = await detailIcon.evaluate((icon) => icon.classList.contains('glyphicon-check'));
      if (currentRead !== initiallyRead) {
        await page.locator('#toggle-read-btn').click();
        await expect(detailIcon).toHaveClass(initiallyRead ? /\bglyphicon-check\b/ : /\bglyphicon-unchecked\b/);
      }
    }
  }

  assertNoPageErrors(errors);
});
