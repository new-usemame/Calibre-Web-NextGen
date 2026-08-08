import { test, expect } from '@playwright/test';

/*
 * #1396 — "Tag view is too dense": the Tags grid showed almost none of the tag
 * text, just columns of "…". Reported by @magdalar with a 720px-wide
 * screenshot, who correctly noted it was "exacerbated by #973".
 *
 * Root cause, measured on the cwn-local library (152 tags) before the fix:
 * #973 added a rename and a delete button to every tag row, and both live
 * INSIDE the grid track. The track was still sized `minmax(220px, 1fr)` from
 * before those buttons existed. Per-row chrome (padding + gap + count badge +
 * two 40px buttons) costs a constant 158px, so at 1280px the name box was 86px
 * of a 244px track — 35% of the cell for the one thing the page exists to
 * show. 132 of 152 tag names (87%) ellipsized.
 *
 * The fix widens the track only where those buttons exist, and lets a name use
 * a second line before ellipsizing. Measured after: 9% desktop, 9% at 720px,
 * 8% mobile — the residue is genuinely long tags.
 *
 * Geometry, not a screenshot: the failure is dimensional, and a pixel diff
 * would also fire on every unrelated restyle.
 */

// Clipped = the box cannot show the full string in EITHER axis. A single-line
// ellipsis clips horizontally (scrollWidth); a line-clamped box clips
// vertically (scrollHeight). Asserting only on width would score a wrapping
// layout as perfect while it silently clamped.
const CLIPPING = `(() => {
  const names = [...document.querySelectorAll('span[class*="name"]')].filter((e) => e.closest('li'));
  if (!names.length) return { total: 0, clipped: 0, pctClipped: 0, nameShareOfTrack: 0 };
  const li = names[0].closest('li');
  const tracks = getComputedStyle(li.closest('ul')).gridTemplateColumns;
  const track = li.getBoundingClientRect().width;
  // Widest name box in the list — the share of the cell the content gets.
  const widest = Math.max(...names.map((n) => n.clientWidth));
  const clipped = names.filter(
    (n) => n.scrollWidth > n.clientWidth + 1 || n.scrollHeight > n.clientHeight + 1).length;
  return {
    total: names.length,
    clipped,
    pctClipped: Math.round((100 * clipped) / names.length),
    nameShareOfTrack: Math.round((100 * widest) / track),
    columns: tracks === 'none' ? 1 : tracks.split(' ').length,
    hasRowActions: li.querySelectorAll('button').length > 0,
  };
})()`;

type Clipping = {
  total: number; clipped: number; pctClipped: number;
  nameShareOfTrack: number; columns: number; hasRowActions?: boolean;
};

for (const [name, width, height] of [
  ['desktop', 1280, 800],
  ['reporter-720', 720, 410],
  ['mobile', 375, 667],
] as const) {
  test(`most tag names are readable rather than ellipsized (${name}, #1396)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/app/tags');
    await page.waitForLoadState('networkidle');
    await page.waitForSelector('li span[class*="name"]', { timeout: 20_000 }).catch(() => {});

    const r = (await page.evaluate(CLIPPING)) as Clipping;
    test.skip(r.total < 20, 'too few tags in this seed to measure grid density');

    expect(
      r.pctClipped,
      `${r.clipped} of ${r.total} tag names are cut off ("…") at ${width}px — the grid track does not leave the name enough room (#1396)`,
    ).toBeLessThanOrEqual(20);
  });
}

test('the wider track is scoped to the list that carries row actions (#1396)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });

  const trackOf = async (route: string) => {
    await page.goto(`/app/${route}`);
    await page.waitForLoadState('networkidle');
    await page.waitForSelector('li span[class*="name"]', { timeout: 20_000 }).catch(() => {});
    return (await page.evaluate(CLIPPING)) as Clipping;
  };

  const tags = await trackOf('tags');
  const authors = await trackOf('authors');
  test.skip(tags.total < 20 || authors.total < 3, 'seed too small to compare grid density');
  test.skip(!tags.hasRowActions, 'this user has no tag-edit rights, so no list carries row actions');

  // Only the Tags grid pays for the #973 buttons, so only it gets the wider
  // track. Widening `.grid` globally would silently thin out Authors, Series
  // and Publishers, which nobody asked for.
  expect(
    authors.columns,
    `Authors dropped to ${authors.columns} columns; the #1396 wider track leaked outside the list that carries row actions`,
  ).toBeGreaterThan(tags.columns);
});

test('the tag name keeps a fair share of its grid cell (#1396)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/app/tags');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('li span[class*="name"]', { timeout: 20_000 }).catch(() => {});

  const r = (await page.evaluate(CLIPPING)) as Clipping;
  test.skip(r.total < 20, 'too few tags in this seed to measure grid density');
  test.skip(!r.hasRowActions, 'this user has no tag-edit rights, so rows carry no action buttons');

  // Pre-fix this was 35%: the #973 buttons took their space out of the name.
  expect(
    r.nameShareOfTrack,
    `the widest tag name gets only ${r.nameShareOfTrack}% of its grid cell; the row's buttons and badge are crowding it out (#1396)`,
  ).toBeGreaterThanOrEqual(45);
});
