import { test, expect } from '@playwright/test';

/*
 * #1166 — the Edit pencil overlaps the series line, and "Read now" renders
 * awkwardly, on narrow cards.
 *
 * Reported by rogovmtlz (Discord, "the edit button overlaps the series"),
 * @iroQuai (a Dutch phone screenshot where "Nu lezen" wraps to two lines beside
 * a 44px pencil) and @HLRobius ("the edit button doesn't resize on mobile with
 * the different settings", with Comfortable/Compact/Dense screenshots).
 *
 * One root cause behind all three: the bottom of the card was not a layout. The
 * pencil was absolutely positioned against `.wrap` and the space for it was
 * *reserved* on the label with a fixed `padding-right: calc(44px + sp-2 * 2)`
 * (#1112). Reservation only works while the card is wider than the thing being
 * reserved. On a 4-column 375px grid the card is ~80px, the reservation is
 * 60px, and the label is left ~20px — so it wraps to two lines, the row grows,
 * and the 44px pencil (bottom: 8px => top at wrap.bottom - 52) rises above the
 * 44px-tall label band into the series line.
 *
 * The fix makes the bottom row a real flex row, so the browser allocates the
 * space instead of the stylesheet guessing at it. These assertions therefore
 * pin *construction*, not pixels: a control that lives in normal flow below the
 * metadata cannot overlap the metadata at any width, density or locale.
 *
 * Runs on the mobile/ipad projects, where coarse-pointer rules make both
 * controls permanently visible — that is the reporters' default state, not a
 * hover edge case.
 */

// Dense is the worst case (4 columns at 375px) and is what @HLRobius shot.
// Set before first paint so the grid never renders at another density.
const DENSITY_KEY = 'cwng:catalog-density-v1';

const MEASURE = `(() => {
  const wraps = [...document.querySelectorAll('[class*="wrap"]')]
    .filter((w) => w.querySelector('[class*="quickEditBtn"]'));

  const overlaps = [];   // pencil sitting on top of card text
  const wrapped = [];    // "Read now" broken onto a second line
  const overflow = [];   // action control escaping the card box
  let checked = 0;

  for (const w of wraps) {
    const pencil = w.querySelector('[class*="quickEditBtn"]');
    if (!pencil || getComputedStyle(pencil).opacity === '0') continue;
    checked++;

    const wb = w.getBoundingClientRect();
    const pb = pencil.getBoundingClientRect();
    const title = (w.querySelector('[class*="title"]')?.textContent || '').slice(0, 30);

    // 1. The pencil must not cover title / author / series.
    for (const sel of ['[class*="title"]', '[class*="author"]', '[data-testid="book-card-series"]']) {
      const el = w.querySelector(sel);
      if (!el) continue;
      const b = el.getBoundingClientRect();
      const v = Math.min(pb.bottom, b.bottom) - Math.max(pb.top, b.top);
      const h = Math.min(pb.right, b.right) - Math.max(pb.left, b.left);
      if (v > 0.5 && h > 0.5) {
        overlaps.push({ title, on: sel, v: Math.round(v), h: Math.round(h) });
      }
    }

    // 2. The "Read now" label must stay on one line. A Range over the text node
    //    reports one client rect per rendered line, which works whether the
    //    label is a bare text node (pre-fix) or wrapped in a span (post-fix).
    const label = w.querySelector('[class*="readNow"]');
    if (label) {
      const textNode = [...label.childNodes]
        .flatMap((n) => (n.nodeType === 3 ? [n] : [...n.childNodes].filter((c) => c.nodeType === 3)))
        .find((n) => n.textContent && n.textContent.trim().length > 1);
      if (textNode) {
        const r = document.createRange();
        r.selectNodeContents(textNode);
        const lines = r.getClientRects().length;
        if (lines > 1) wrapped.push({ title, lines, text: textNode.textContent.trim() });
      }
      // 3. Nothing in the action row may spill outside the card.
      const lb = label.getBoundingClientRect();
      if (lb.right - wb.right > 1 || wb.left - lb.left > 1) {
        overflow.push({ title, el: 'readNow', by: Math.round(Math.max(lb.right - wb.right, wb.left - lb.left)) });
      }
    }
    if (pb.right - wb.right > 1 || wb.left - pb.left > 1) {
      overflow.push({ title, el: 'pencil', by: Math.round(Math.max(pb.right - wb.right, wb.left - pb.left)) });
    }
  }
  return { checked, overlaps, wrapped, overflow };
})()`;

for (const density of ['dense', 'compact', 'comfortable'] as const) {
  test(`card actions never cover the metadata (${density} grid, #1166)`, async ({ page }) => {
    // usePersistentChoice stores the RAW string and ignores anything not in its
    // allowed list — a JSON-encoded '"dense"' silently falls back to the
    // default, which would run all three cases at the same density and look
    // like coverage it isn't.
    await page.addInitScript(
      ([key, value]) => window.localStorage.setItem(key, value),
      [DENSITY_KEY, density] as const,
    );
    await page.goto('/app/');
    await page.waitForLoadState('networkidle');
    // The grid animates in; measure settled geometry.
    await page.locator('[class*="quickEditBtn"]').first().waitFor({ state: 'attached' });

    // Fail loudly if the density never applied, rather than passing on a grid
    // that was never the one under test. Assert the CLASS, not a column count:
    // the fixed 2/3/4-column layout only exists below the 600px breakpoint, so
    // a count would make this spec viewport-specific for no benefit.
    const applied = await page.evaluate(`(() => {
      const g = [...document.querySelectorAll('[class*="grid"]')]
        .find((e) => /density_/.test(e.className));
      return g ? (g.className.match(/density_(\\w+?)_/) || [])[1] || g.className : null;
    })()`) as string | null;
    expect(applied, `the ${density} grid did not apply (grid reports "${applied}")`).toBe(density);

    const r = await page.evaluate(MEASURE) as {
      checked: number;
      overlaps: { title: string; on: string; v: number; h: number }[];
      wrapped: { title: string; lines: number; text: string }[];
      overflow: { title: string; el: string; by: number }[];
    };

    test.skip(r.checked === 0, 'no card in this seed shows an edit control');

    expect(r.overlaps,
      `the Edit control sits on top of card text — the reporters' symptom (#1166): ${JSON.stringify(r.overlaps)}`
    ).toEqual([]);

    expect(r.wrapped,
      `the "Read now" label wrapped onto a second line because the reserved corner left it almost no width (#1166): ${JSON.stringify(r.wrapped)}`
    ).toEqual([]);

    expect(r.overflow,
      `a card action escaped the card box (#1166): ${JSON.stringify(r.overflow)}`
    ).toEqual([]);
  });
}

/*
 * The pure form of rogovmtlz's "the edit button overlaps the series": a book
 * with no readable format (a MOBI/AZW3-only library — neither is in
 * readerTarget's SPA_READABLE or SERVER_READABLE sets) renders no "Read now"
 * link at all. Before the fix the pencil was absolutely positioned against the
 * card, so with nothing at the bottom to sit over it landed straight on the
 * last metadata line.
 *
 * The cwn-local seed is all EPUB, so every card has a read target and that
 * state cannot occur naturally here. Remove the read link from a card and
 * re-measure instead: it is the same DOM the reporter's library produces, and
 * it pins the property that actually matters — the pencil's position is not a
 * function of whether a sibling happens to exist.
 */
test('the Edit control stays off the metadata when a book has no readable format (#1166)', async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    ['cwng:catalog-density-v1', 'comfortable'] as const,
  );
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');
  await page.locator('[class*="quickEditBtn"]').first().waitFor({ state: 'attached' });

  const r = await page.evaluate(`(() => {
    const card = [...document.querySelectorAll('[class*="wrap"]')]
      .find((w) => w.querySelector('[class*="quickEditBtn"]') && w.querySelector('[class*="readNow"]'));
    if (!card) return { simulated: false };
    card.querySelector('[class*="readNow"]').remove();

    const pencil = card.querySelector('[class*="quickEditBtn"]');
    const pb = pencil.getBoundingClientRect();
    const cb = card.getBoundingClientRect();
    // Alone in the row, the pencil must still sit at the RIGHT edge. A bare
    // flex row would start-align it, silently moving a control users have
    // always found bottom-right — and only on the cards this fix targets.
    const distFromRight = Math.round(cb.right - pb.right);
    const distFromLeft = Math.round(pb.left - cb.left);
    const hits = [];
    for (const sel of ['[class*="title"]', '[class*="author"]', '[data-testid="book-card-series"]']) {
      const el = card.querySelector(sel);
      if (!el) continue;
      const b = el.getBoundingClientRect();
      const v = Math.min(pb.bottom, b.bottom) - Math.max(pb.top, b.top);
      const h = Math.min(pb.right, b.right) - Math.max(pb.left, b.left);
      if (v > 0.5 && h > 0.5) hits.push({ on: sel, v: Math.round(v), h: Math.round(h) });
    }
    return { simulated: true, hits, distFromRight, distFromLeft };
  })()`) as {
    simulated: boolean;
    hits?: { on: string; v: number; h: number }[];
    distFromRight?: number;
    distFromLeft?: number;
  };

  test.skip(!r.simulated, 'no card in this seed has both a read link and an edit control');

  expect(r.hits,
    `with no "Read now" link the Edit control drops onto the card's metadata (#1166): ${JSON.stringify(r.hits)}`
  ).toEqual([]);

  expect(r.distFromRight!,
    `the lone Edit control drifted off the right edge (${r.distFromRight}px from right, ${r.distFromLeft}px from left) — it should stay bottom-right (#1166)`
  ).toBeLessThan(r.distFromLeft!);
});

/*
 * Letting the row size itself only helps while what it hands each control is
 * still tappable. `.readNow` used to floor at min-width: 0, so on the narrowest
 * real screen — 280px, a folded Galaxy Fold — a 4-column dense card is 58px and
 * the link measured 22px wide, under WCAG 2.2 SC 2.5.8's 24x24 minimum.
 *
 * 280px dense is the worst case the app can be put in, which is why it is the
 * width asserted here: every wider grid clears the floor with room to spare.
 */
test('both card actions stay at a tappable size on the narrowest screens (#1166)', async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    ['cwng:catalog-density-v1', 'dense'] as const,
  );
  await page.setViewportSize({ width: 280, height: 800 });
  await page.goto('/app/');
  await page.waitForLoadState('networkidle');
  await page.locator('[class*="quickEditBtn"]').first().waitFor({ state: 'attached' });

  const r = await page.evaluate(`(() => {
    const card = [...document.querySelectorAll('[class*="wrap"]')]
      .find((w) => w.querySelector('[class*="quickEditBtn"]'));
    if (!card) return { found: false };
    const cb = card.getBoundingClientRect();
    const small = [];
    for (const [name, sel] of [['Read now', '[class*="readNow"]:not([class*="Label"])'], ['Edit', '[class*="quickEditBtn"]']]) {
      const el = card.querySelector(sel);
      if (!el || getComputedStyle(el).opacity === '0') continue;
      const b = el.getBoundingClientRect();
      if (b.width < 24 || b.height < 24) small.push({ name, w: Math.round(b.width), h: Math.round(b.height) });
    }
    return { found: true, cardWidth: Math.round(cb.width), small };
  })()`) as { found: boolean; cardWidth?: number; small?: { name: string; w: number; h: number }[] };

  test.skip(!r.found, 'no card with an edit control in this seed');

  expect(r.small,
    `a card action shrank below the 24x24 minimum target size on a ${r.cardWidth}px card (WCAG 2.2 SC 2.5.8, #1166): ${JSON.stringify(r.small)}`
  ).toEqual([]);
});
