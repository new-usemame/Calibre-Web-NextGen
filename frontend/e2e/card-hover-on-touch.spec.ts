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
    await page.keyboard.press('Tab');
    await expect.poll(() => first.evaluate((n) => n === document.activeElement)).toBe(true);
    const focused = await liftState(page);
    expect(focused.transform, 'keyboard focus lifts the cover').not.toBe('none');
  }
});
