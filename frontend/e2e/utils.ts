import { Page, expect } from '@playwright/test';

/** Attach a console/pageerror collector. A clean console is a test result, not
 *  decoration — this is what catches the `[object Object]` error-envelope class. */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console.error: ${m.text()}`);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

/** Known-benign console noise to ignore (favicon 404s, devtools, etc.). Keep
 *  this list SHORT and justified — every entry is a muted signal. */
const BENIGN = [
  /favicon/i,
  /Failed to load resource.*404.*(favicon|\.map)/i,
];

export function assertNoPageErrors(errors: string[]) {
  const real = errors.filter((e) => !BENIGN.some((b) => b.test(e)));
  expect(real, `unexpected console/page errors:\n${real.join('\n')}`).toEqual([]);
}

/** No horizontal body overflow — the signature of the mobile-reflow regressions
 *  (#288 banner, #576 drawer, edit-cover at 375px).
 *
 *  On failure this names the elements that stick out past the viewport. Without
 *  that, the failure reads "expected <= 1, received 35" and says nothing about
 *  WHICH element is 35px wide of the edge — which is exactly why CI's detail-page
 *  overflow sat behind a "pre-existing" label across several releases while nobody
 *  could act on it (`notes/e2e-mobile-overflow-ci-triage.md`). The dump only runs
 *  when the assertion is about to fail, so it costs a passing run nothing. */
export async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await pageOverflow(page);
  const detail = overflow > 1 ? `\n${await describeOverflowingElements(page)}` : '';
  expect(
    overflow,
    `page scrolls horizontally (mobile reflow regression)${detail}`,
  ).toBeLessThanOrEqual(1);
}

/** The elements extending past the viewport's right edge, deepest first.
 *
 *  Deepest-first matters: an overflowing leaf drags every ancestor out with it, so
 *  the shallow entries are consequences and the deep ones are the cause. Reports
 *  the computed properties that actually produce this class of bug (`white-space`,
 *  `max-width`, `overflow-wrap`, `min-width`) so the fix is usually readable
 *  straight off the failure message.
 *
 *  Elements clipped by a scrollable ancestor are EXCLUDED. A carousel or shelf row
 *  with `overflow-x: auto` legitimately holds children past the viewport edge, and
 *  their excess never reaches `documentElement.scrollWidth` — the catalog grid
 *  reports dozens of them while the page overflow is 0. Listing those would point
 *  the reader at innocent elements, which is worse than printing nothing. */
export async function describeOverflowingElements(page: Page, limit = 8): Promise<string> {
  const rows = await page.evaluate((max) => {
    const viewport = document.documentElement.clientWidth;
    const out: Array<Record<string, string | number>> = [];
    /** True when some ancestor clips/scrolls horizontally, absorbing the excess. */
    const isAbsorbed = (el: HTMLElement) => {
      for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') return true;
      }
      return false;
    };
    document.querySelectorAll<HTMLElement>('*').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      if (r.right <= viewport + 1) return;
      if (isAbsorbed(el)) return;
      const cs = getComputedStyle(el);
      let depth = 0;
      for (let p: HTMLElement | null = el; p?.parentElement; p = p.parentElement) depth += 1;
      out.push({
        depth,
        tag: el.tagName.toLowerCase(),
        cls: String((el as unknown as { className?: string }).className ?? '').slice(0, 60),
        testid: el.getAttribute('data-testid') ?? '',
        text: (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40),
        past: Math.round(r.right - viewport),
        width: Math.round(r.width),
        whiteSpace: cs.whiteSpace,
        maxWidth: cs.maxWidth,
        overflowWrap: cs.overflowWrap,
        minWidth: cs.minWidth,
      });
    });
    out.sort((a, b) => (b.depth as number) - (a.depth as number));
    return out.slice(0, max);
  }, limit);

  if (rows.length === 0) {
    // A border box can sit inside the viewport and still push the document wider:
    // scrollable overflow counts a child's MARGIN box, and a pseudo-element has no
    // node for the sweep above to find. CI's long-standing 35px detail-page
    // overflow lands here — every element is within the edge — so an empty list
    // would be the least useful thing to print. Fall back to the two measurements
    // that do explain this case.
    return describeIndirectOverflow(page, limit);
  }
  return rows
    .map(
      (r) =>
        `  ${r.past}px past @ depth ${r.depth}: <${r.tag}${r.testid ? ` data-testid="${r.testid}"` : ''} class="${r.cls}">` +
        ` w=${r.width} white-space=${r.whiteSpace} max-width=${r.maxWidth}` +
        ` overflow-wrap=${r.overflowWrap} min-width=${r.minWidth}` +
        (r.text ? `\n      text: ${JSON.stringify(r.text)}` : ''),
    )
    .join('\n');
}

/** Horizontal overflow in px, for specs that need to compare one render against
 *  another rather than assert the absolute property. Asserting "no overflow at
 *  all" makes a spec fail for any unrelated overflow the page happens to carry,
 *  which tells you nothing about the element under test. */
export async function pageOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const el = document.documentElement;
    return el.scrollWidth - el.clientWidth;
  });
}

/** Explains overflow that no element's border box accounts for.
 *
 *  Two causes produce a wider document while every rect stays inside the viewport:
 *
 *  1. A **margin**. Scrollable overflow includes a child's margin box, so an
 *     element ending at 388px with `margin-right: 37px` widens the document to
 *     425px while its own `right` is comfortably within the edge.
 *  2. Content wider than its own box — a **pseudo-element**, a transform, or a
 *     min-width floor — on an element that is not a scroll container. Such an
 *     element has `scrollWidth > clientWidth` while its rect stays put, and it
 *     hands the excess up to its parent.
 *
 *  Reporting the containers in (2) deepest-first localises the cause to one box
 *  even when the thing inside it has no node of its own. */
async function describeIndirectOverflow(page: Page, limit: number): Promise<string> {
  const found = await page.evaluate((max) => {
    const viewport = document.documentElement.clientWidth;
    const depthOf = (el: HTMLElement) => {
      let d = 0;
      for (let p: HTMLElement | null = el; p?.parentElement; p = p.parentElement) d += 1;
      return d;
    };
    const scrolls = (el: HTMLElement) => {
      const ox = getComputedStyle(el).overflowX;
      return ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip';
    };
    const absorbed = (el: HTMLElement) => {
      for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
        if (scrolls(p)) return true;
      }
      return false;
    };

    const margins: string[] = [];
    const inner: string[] = [];

    document.querySelectorAll<HTMLElement>('*').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      if (absorbed(el)) return;
      const cs = getComputedStyle(el);
      const label =
        `<${el.tagName.toLowerCase()}` +
        `${el.getAttribute('data-testid') ? ` data-testid="${el.getAttribute('data-testid')}"` : ''}` +
        ` class="${String((el as unknown as { className?: string }).className ?? '').slice(0, 60)}">`;

      const marginRight = parseFloat(cs.marginRight) || 0;
      if (marginRight > 0 && r.right + marginRight > viewport + 1) {
        margins.push(
          `  ${Math.round(r.right + marginRight - viewport)}px past (margin box) @ depth ${depthOf(el)}: ${label}` +
            ` right=${Math.round(r.right)} margin-right=${cs.marginRight}`,
        );
      }

      const excess = el.scrollWidth - el.clientWidth;
      if (excess > 1 && !scrolls(el)) {
        const before = getComputedStyle(el, '::before').content;
        const after = getComputedStyle(el, '::after').content;
        const pseudo = [
          before && before !== 'none' ? '::before' : '',
          after && after !== 'none' ? '::after' : '',
        ]
          .filter(Boolean)
          .join('+');
        inner.push(
          `  holds ${excess}px more content than its box @ depth ${depthOf(el)}: ${label}` +
            ` client=${el.clientWidth} scroll=${el.scrollWidth} min-width=${cs.minWidth}` +
            ` transform=${cs.transform === 'none' ? 'none' : 'yes'}${pseudo ? ` pseudo=${pseudo}` : ''}`,
        );
      }
    });

    const byDepthDesc = (a: string, b: string) =>
      Number(/@ depth (\d+)/.exec(b)?.[1] ?? 0) - Number(/@ depth (\d+)/.exec(a)?.[1] ?? 0);
    return {
      margins: margins.sort(byDepthDesc).slice(0, max),
      inner: inner.sort(byDepthDesc).slice(0, max),
    };
  }, limit);

  const parts: string[] = [
    '  (no element’s border box crosses the viewport edge — the width comes from a margin, a pseudo-element, a transform, or a min-width floor)',
  ];
  if (found.margins.length) parts.push('  margin boxes past the edge:', ...found.margins);
  if (found.inner.length) parts.push('  boxes whose content is wider than they are:', ...found.inner);
  if (!found.margins.length && !found.inner.length) {
    parts.push('  nothing matched either probe — inspect documentElement/body padding directly');
  }
  return parts.join('\n');
}
