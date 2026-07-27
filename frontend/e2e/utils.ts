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
 *  (#288 banner, #576 drawer, edit-cover at 375px). */
export async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await pageOverflow(page);
  expect(overflow, 'page scrolls horizontally (mobile reflow regression)').toBeLessThanOrEqual(1);
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
