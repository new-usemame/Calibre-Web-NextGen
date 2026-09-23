import { test, expect } from '@playwright/test';

/*
 * The card "lift" (raise + shadow + accent ring on the cover) is a hover
 * affordance. iOS Safari applies a synthetic :hover to whatever was last
 * tapped and keeps it until the next tap lands elsewhere, so on a phone the
 * ring stayed lit under a cover the reader had touched while scrolling and
 * never opened (operator recording, 2026-09-12). On a touch device the lift
 * must therefore never follow the pointer at all; it is a pointer affordance
 * on devices that can hover, and a keyboard affordance (focus-visible)
 * everywhere.
 */

const isTouchProject = () => test.info().project.use.hasTouch === true;

// A lifted cover is raised (non-identity transform) and carries the accent ring.
async function liftState(page: import('@playwright/test').Page) {
  const cover = page.locator('a[aria-label^="Open details for"]').first().locator('div').first();
  return cover.evaluate((node) => {
    const s = getComputedStyle(node);
    return { transform: s.transform, outline: s.outlineColor };
  });
}

const TRANSPARENT = /^(transparent|rgba\(0, 0, 0, 0\))$/;

const RING_OFF = /^(transparent|rgba\(0, 0, 0, 0\))$/;

test('the cover lift never follows the pointer on a touch device', async ({ page }) => {
  await page.goto('/app');
  const first = page.locator('a[aria-label^="Open details for"]').first();
  await expect(first).toBeVisible();

  const box = (await first.boundingBox())!;
  const media = await page.evaluate(() => ({
    hoverNone: matchMedia('(hover: none)').matches,
    coarse: matchMedia('(pointer: coarse)').matches,
  }));
  await test.info().attach('media', { body: JSON.stringify(media), contentType: 'application/json' });

  // The pointer sitting over the cover is exactly what a synthetic hover is.
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 3);
  await page.waitForTimeout(300);
  const hovered = await liftState(page);

  if (isTouchProject()) {
    expect(hovered.transform, 'touch: a hovered cover must not be raised').toBe('none');
    expect(hovered.outline, 'touch: a hovered cover must not carry the ring').toMatch(RING_OFF);
  } else {
    expect(hovered.transform, 'mouse: hover still lifts the cover').not.toBe('none');
    expect(hovered.outline, 'mouse: hover still draws the ring').not.toMatch(RING_OFF);

    // Keyboard users keep the lift as their focus cue.
    await page.mouse.move(0, 0);
    // Land on the card by keyboard: step back off it and Tab forward onto it,
    // so the final focus move is a keystroke and :focus-visible applies.
    await first.focus();
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect.poll(() => first.evaluate((n) => n === document.activeElement),
      { message: 'keyboard focus lands on the first card' }).toBe(true);
    const focused = await liftState(page);
    expect(focused.transform, 'keyboard focus lifts the cover').not.toBe('none');
  }
});

test('a touch that becomes a scroll leaves no highlight on the card', async ({ page, browserName }) => {
  test.skip(!isTouchProject(), 'touch-only regression; the fine-pointer lift is covered above');

  await page.goto('/app');
  const firstCard = page.locator('a[aria-label^="Open details for"]').first();
  await expect(firstCard).toBeVisible();
  const firstCover = firstCard.locator('div').first();

  const sampleCover = () => firstCover.evaluate((node) => {
    const s = getComputedStyle(node);
    return { transform: s.transform, boxShadow: s.boxShadow, outline: s.outlineColor };
  });
  const resting = await sampleCover();
  expect(resting.transform, 'a card at rest is not raised').toBe('none');

  /* iOS Safari paints its default tap highlight — a grey box BEHIND the link —
     on every touch that starts on a cover, including touches that turn into a
     scroll and never open the book (operator, 2026-09-14: "the little highlight
     that goes behind the tapped on but not necessarily opened book"). The card
     opts out at its root so no touch can paint it; scoped to the card, not
     global, per the operator's constraint. */
  const tapHighlight = await firstCard.evaluate(
    (node) => getComputedStyle(node).getPropertyValue('-webkit-tap-highlight-color'),
  );
  await test.info().attach('tap-highlight', { body: tapHighlight, contentType: 'text/plain' });
  expect(tapHighlight, 'the card itself must be unable to paint a tap highlight').toMatch(TRANSPARENT);

  if (browserName !== 'chromium') {
    /* Playwright drives one-shot taps on WebKit; the press-and-drag gesture
       below needs CDP. WebKit's guarantee is the property assertion above —
       a transparent highlight cannot paint — plus the hover gate the previous
       test measures on this same project. */
    return;
  }

  /* A real gesture through the input pipeline, not dispatched DOM events: the
     finger lands on the cover, drags the page into a scroll, lifts. This is
     the operator's "scrolling and tapping around" path — the touch starts on
     the card but must not open the book and must leave nothing behind. */
  const box = (await firstCard.boundingBox())!;
  const x = Math.round(box.x + box.width / 2);
  const y = Math.round(box.y + box.height / 2);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y, id: 1 }] });
  for (let step = 1; step <= 5; step++) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove', touchPoints: [{ x, y: y - step * 24, id: 1 }],
    });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

  // The gesture became a scroll, not a tap: the catalog is still here…
  await expect(page, 'a scroll starting on a card must not open the book').toHaveURL(/\/app\/?$/);
  // …and within 300 ms the card computes exactly as it did at rest — no stuck
  // lift, shadow or ring (the glitch was the highlight lingering under a cover
  // the reader touched while scrolling and never opened).
  await page.waitForTimeout(300);
  const after = await sampleCover();
  expect(after.transform, 'scroll-off leaves no lift').toBe(resting.transform);
  expect(after.boxShadow, 'scroll-off leaves no shadow').toBe(resting.boxShadow);
  expect(after.outline, 'scroll-off leaves no ring').toBe(resting.outline);
});
