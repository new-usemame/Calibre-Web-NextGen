import { test, expect } from '@playwright/test';

/*
 * Fork issue #2051 (reported by @magdalar): in the library grid a series name
 * long enough to be ellipsised took the book's position with it, so a card read
 * "The Stormlight Archive Chronicles of Ro…" with no way to tell which volume
 * it was. The position has to stay visible however long the name is.
 *
 * Seed-independent: the first book of the catalog response is rewritten to
 * carry a very long series name at position 4, so the check does not depend on
 * the library having such a series.
 */

const LONG_SERIES =
  'The Extraordinarily Long Chronicles of the Seventh Kingdom Beyond the Mountains';

test('#2051 a truncated series name keeps its position visible on the card', async ({ page }) => {
  // The catalog can issue more than one books request (grid pages, refetches),
  // so the book to rewrite is fixed by the first response and rewritten in
  // every later one; the line is then found by its text, not by a card id.
  let targetId: number | null = null;
  await page.route(/\/api\/v1\/books(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (targetId === null && body.items?.[0]) targetId = body.items[0].id;
    for (const item of body.items ?? []) {
      if (item.id === targetId) {
        item.series = LONG_SERIES;
        item.series_index = 4;
      }
    }
    await route.fulfill({ response, json: body });
  });

  await page.goto('/app/');
  const line = page.getByTestId('book-card-series').filter({ hasText: LONG_SERIES }).first();
  await expect(line).toBeVisible();

  const geometry = await line.evaluate((el) => {
    const box = el.getBoundingClientRect();
    // Find the "#4" characters wherever they are in the rendered text.
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let node: Node | null;
    while ((node = walker.nextNode())) {
      const at = (node.textContent ?? '').indexOf('#4');
      if (at < 0) continue;
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + 2);
      const r = range.getBoundingClientRect();
      return {
        found: true,
        inside:
          r.width > 0 &&
          r.left >= box.left - 0.5 &&
          r.right <= box.right + 0.5 &&
          r.top >= box.top - 0.5 &&
          r.bottom <= box.bottom + 0.5,
        index: { left: r.left, right: r.right, top: r.top, bottom: r.bottom },
        line: { left: box.left, right: box.right, top: box.top, bottom: box.bottom },
        // Width overflow for an ellipsised span; height overflow for a
        // line-clamped block that hides the wrapped remainder.
        truncated: el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight ||
          Array.from(el.querySelectorAll('span')).some((s) => s.scrollWidth > s.clientWidth),
      };
    }
    return { found: false, inside: false, index: null, line: null, truncated: false };
  });

  expect(geometry.found, 'the position is rendered at all').toBe(true);
  // The name really is long enough to be cut on this viewport; otherwise the
  // test would pass without exercising the bug.
  expect(geometry.truncated, 'the series name is ellipsised').toBe(true);
  expect(geometry.inside, `"#4" is inside the visible series line: ${JSON.stringify(geometry)}`).toBe(true);
  // The full text stays available on hover.
  await expect(line).toHaveAttribute('title', `${LONG_SERIES} #4`);
});
