import { test, expect } from '@playwright/test';

/*
 * Fork issue #2147: on a New UI book card the series line was one line, so a
 * name that did not fit was ellipsised ("The Lord of the Rin…") where the
 * classic view wraps it. #2051 already pinned the position beside the name; this
 * lets the name itself take a second line before it is ellipsised.
 *
 * The line keeps a two-line height whether the name needs one line or two, so
 * neighbouring cards keep the same baseline (the title does the same, #863).
 *
 * Seed-independent: the first two books of the catalog response are rewritten
 * to carry a short and a two-line series name at position 4.
 */

const SHORT_SERIES = 'Dune';
const WRAPPING_SERIES = 'Chronicles of the Seventh Kingdom';

test('#2147 a series name wraps to a second line before it is ellipsised', async ({ page }) => {
  const targets: Record<number, string> = {};
  await page.route(/\/api\/v1\/books(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const items: Array<{ id: number; series?: string; series_index?: number; series_id?: number }> = body.items ?? [];
    // Fix the two books on the first response; rewrite them on every later one.
    if (Object.keys(targets).length === 0) {
      if (items[0]) targets[items[0].id] = SHORT_SERIES;
      if (items[1]) targets[items[1].id] = WRAPPING_SERIES;
    }
    for (const item of items) {
      if (targets[item.id]) {
        item.series = targets[item.id];
        item.series_index = 4;
        // A series id makes the card render the line as a link to the series
        // page, which is what a real library shows. The link variant carries
        // its own min-height, so the reserved height has to hold there.
        item.series_id = 999_000 + item.id;
      }
    }
    await route.fulfill({ response, json: body });
  });

  await page.goto('/app/');
  const shortLine = page.getByTestId('book-card-series').filter({ hasText: SHORT_SERIES }).first();
  const wrapLine = page.getByTestId('book-card-series').filter({ hasText: WRAPPING_SERIES }).first();
  await expect(shortLine).toBeVisible();
  await expect(wrapLine).toBeVisible();

  const m = await wrapLine.evaluate((el) => {
    const nameEl = (Array.from(el.querySelectorAll('span')) as HTMLElement[])
      .find((s) => (s.textContent ?? '').includes('Seventh'))!;
    const lineHeight = parseFloat(getComputedStyle(nameEl).lineHeight);
    return {
      lines: Math.round(nameEl.scrollHeight / lineHeight),
      visibleLines: Math.round(nameEl.clientHeight / lineHeight),
      cardWidth: el.getBoundingClientRect().width,
    };
  });

  // Whether this name needs two lines or three depends on the card width and
  // the platform's font metrics, so the check is on what the user sees: the
  // name gets a second line instead of being cut on the first, and never more
  // than two, so the card layout stays fixed.
  expect(m.visibleLines, `the name shows on two lines, not one: ${JSON.stringify(m)}`).toBe(2);

  // The line reserves the same height whether the name needs one line or two.
  const [shortBox, wrapBox] = await Promise.all([shortLine.boundingBox(), wrapLine.boundingBox()]);
  expect(shortBox && wrapBox).toBeTruthy();
  expect(Math.abs(shortBox!.height - wrapBox!.height)).toBeLessThan(1);
});
