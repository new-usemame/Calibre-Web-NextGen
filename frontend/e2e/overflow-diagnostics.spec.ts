import { test, expect } from '@playwright/test';
import { describeOverflowingElements, pageOverflow, assertNoHorizontalOverflow } from './utils';

/*
 * Pins the failure DIAGNOSTIC, not a product behaviour.
 *
 * `assertNoHorizontalOverflow` guards a whole class of mobile-reflow bugs, but
 * for a long time its failure said only "expected <= 1, received 35" — no element,
 * no CSS. CI's book detail page carried exactly that for several releases and the
 * failure was annotated "pre-existing" rather than fixed, because nothing in the
 * message told anyone where to look (`notes/e2e-mobile-overflow-ci-triage.md`).
 *
 * A diagnostic that silently stops naming the offender would put us straight back
 * there, and it would do so invisibly — every spec would still pass. Hence a test.
 */

test('the overflow reporter names the offending element and its CSS', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');

  // Baseline first: if the page under test already overflows, a passing assertion
  // below would prove nothing about the element we inject.
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);

  await page.evaluate(() => {
    const el = document.createElement('div');
    el.setAttribute('data-testid', 'overflow-probe');
    el.textContent = 'probe';
    el.style.cssText =
      'position:absolute;top:0;left:0;width:480px;height:8px;white-space:nowrap';
    document.body.appendChild(el);
  });

  const overflow = await pageOverflow(page);
  expect(overflow, 'the probe should push the document past 390px').toBeGreaterThan(1);

  const report = await describeOverflowingElements(page);
  expect(report, 'the report must name the element by test id').toContain('overflow-probe');
  expect(report, 'the report must carry the CSS that causes this bug class').toContain(
    'white-space=nowrap',
  );
  expect(report, 'the report must say how far past the edge it lands').toMatch(/\d+px past/);

  // And the assertion itself must surface that report, not just the number —
  // this is the path a real failing spec takes.
  const failure = await assertNoHorizontalOverflow(page).then(
    () => null,
    (e: Error) => e.message,
  );
  expect(failure, 'the assertion should have failed while the probe is present').toBeTruthy();
  expect(failure!).toContain('overflow-probe');

  await page.evaluate(() => document.querySelector('[data-testid="overflow-probe"]')?.remove());
  await assertNoHorizontalOverflow(page);
});

test('the overflow reporter explains overflow no border box accounts for', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);

  // A pseudo-element is the case that actually reproduces this shape, and it is a
  // strong candidate for CI's own 35px: `::after` has no node, so it is invisible
  // to querySelectorAll, yet it widens its parent all the same. Its parent then
  // reads `scrollWidth > clientWidth` while every border box stays inside the
  // viewport — which is exactly the state CI reports.
  //
  // Note a right MARGIN does not produce this in Chromium: it does not extend the
  // document's scrollable overflow, so it cannot be the cause on its own. The
  // reporter still measures margin boxes, but as a secondary signal.
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.setAttribute('data-testid', 'pseudo-host');
    host.style.cssText = 'position:absolute;top:0;left:0;width:100px;height:10px';
    const style = document.createElement('style');
    style.setAttribute('data-testid', 'pseudo-style');
    style.textContent =
      '[data-testid="pseudo-host"]::after{content:"";display:block;width:900px;height:10px}';
    document.head.appendChild(style);
    document.body.appendChild(host);
  });

  expect(
    await pageOverflow(page),
    'the pseudo-element should widen the document',
  ).toBeGreaterThan(1);

  const report = await describeOverflowingElements(page);
  expect(report, 'must say the border boxes are all inside the edge').toContain(
    'no element’s border box crosses the viewport edge',
  );
  expect(report, 'must localise the box holding the oversized content').toContain(
    'more content than its box',
  );
  expect(report, 'must name that box').toContain('pseudo-host');
  expect(report, 'must flag that the box carries a pseudo-element').toContain('pseudo=::after');

  await page.evaluate(() => {
    document.querySelector('[data-testid="pseudo-host"]')?.remove();
    document.querySelector('[data-testid="pseudo-style"]')?.remove();
  });
  await assertNoHorizontalOverflow(page);
});

test('the overflow reporter ignores children a scrollable ancestor absorbs', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');

  // A carousel/shelf row holds children well past the viewport edge on purpose;
  // the scroll container absorbs them, so the document does NOT overflow. Naming
  // them in a failure report sends the reader after elements that are behaving —
  // which is exactly what this reporter did on its first draft, listing book-card
  // titles 139px "past" the edge on a page whose real overflow was 0.
  await page.evaluate(() => {
    const scroller = document.createElement('div');
    scroller.style.cssText = 'position:absolute;top:0;left:0;width:200px;overflow-x:auto';
    const wide = document.createElement('div');
    wide.setAttribute('data-testid', 'absorbed-probe');
    wide.style.cssText = 'width:900px;height:8px;white-space:nowrap';
    scroller.appendChild(wide);
    document.body.appendChild(scroller);
  });

  expect(
    await pageOverflow(page),
    'a scrollable ancestor should absorb its children — the document must not overflow',
  ).toBeLessThanOrEqual(1);
  expect(
    await describeOverflowingElements(page),
    'an absorbed child must not be reported as an offender',
  ).not.toContain('absorbed-probe');

  await page.evaluate(() =>
    document.querySelector('[data-testid="absorbed-probe"]')?.parentElement?.remove(),
  );
});
