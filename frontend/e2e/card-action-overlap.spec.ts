import { test, expect } from '@playwright/test';

/*
 * #1112 — the Edit pencil overlapped the "Read now" label on a book card.
 *
 * Reported by @Andrew-H2O after the vertical-alignment fix on #863, with a
 * 335px-wide screenshot: the label read "Read no…" because the pencil sat on
 * top of its tail. At the time this shipped, the coarse-pointer block made both
 * controls permanently visible. The operator reversed that ruling on
 * 2026-08-29; the probe now focuses each pencil before measuring, so it still
 * pins the visible/focused geometry instead of silently checking zero cards.
 *
 * Measured on a real library before the fix: 52px of the label ran under the
 * button at 360px and at 768px. After: 0px.
 *
 * Geometry rather than a screenshot, because the failure is positional and a
 * pixel diff would also fire on every unrelated restyle.
 */

// IIFE, not a bare arrow function. page.evaluate() given a STRING evaluates it
// as an expression, so `() => {...}` returns the function itself — which is not
// serializable, so it arrives as undefined and every assertion dies on
// "Cannot read properties of undefined". Shipped that way in #1112 and caught
// by the e2e gate the first time it ran (#953), which is the gate earning its
// keep on its first outing.
const OVERLAP = `(() => {
  const labels = [...document.querySelectorAll('*')].filter(
    (e) => typeof e.className === 'string' && /readNow/.test(e.className));
  let checked = 0, worst = 0, unrevealed = 0;
  for (const label of labels) {
    const card = label.closest('[class*="wrap"]');
    if (!card) continue;
    const pencil = card.querySelector('[class*="quickEditBtn"]');
    if (!pencil) continue;
    pencil.style.transition = 'none';
    label.style.transition = 'none';
    pencil.focus();
    checked++;
    if (getComputedStyle(pencil).opacity === '0' || getComputedStyle(label).opacity === '0') {
      unrevealed++;
      continue;
    }
    const lb = label.getBoundingClientRect();
    const pb = pencil.getBoundingClientRect();
    // Content box: padding is the reserved corner, so exclude it.
    const contentRight = lb.right - parseFloat(getComputedStyle(label).paddingRight);
    worst = Math.max(worst, Math.round(contentRight - pb.left));
  }
  return { checked, worst, unrevealed };
})()`;

for (const [name, width, height] of [['phone', 360, 760], ['tablet', 768, 1024]] as const) {
  test(`the Edit control never covers the "Read now" label (${name}, #1112)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/app/');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(OVERLAP) as { checked: number; worst: number; unrevealed: number };
    test.skip(result.checked === 0, 'no card in this seed shows both a read link and an edit control');

    expect(result.unrevealed, 'keyboard focus must reveal both card actions before geometry is measured').toBe(0);

    expect(result.worst,
      `the "Read now" label runs ${result.worst}px under the Edit control, clipping its tail (#1112)`
    ).toBeLessThanOrEqual(0);
  });
}
