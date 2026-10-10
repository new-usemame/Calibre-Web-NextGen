import { test, expect, type Page } from '@playwright/test';

// #2341: on /account/devices the top bar scrolled away while the sidebar rail
// stayed pinned below where the bar should be, and the collapsed rail opened
// with an empty slot (the invisible "Pin sidebar" control) above Library.

async function chromeAfterScroll(page: Page) {
  // Scroll the document itself, whatever the page's content height, so the
  // assertion is about the shell chrome and not about how many devices exist.
  await page.evaluate(() => {
    const spacer = document.createElement('div');
    spacer.style.height = '2000px';
    document.getElementById('main')?.appendChild(spacer);
    window.scrollTo(0, 600);
  });
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  return page.evaluate(() => {
    const bar = document.querySelector('header')!.getBoundingClientRect();
    const rail = document.querySelector('nav[aria-label="Browse"]')!.parentElement!.getBoundingClientRect();
    return { barTop: bar.top, barBottom: bar.bottom, railTop: rail.top };
  });
}

for (const route of ['/app/account/devices', '/app/account']) {
  test(`top bar stays at the top of a scrolled ${route}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');
    await page.goto(route);
    await expect(page.getByRole('navigation', { name: 'Browse' })).toBeVisible();

    const chrome = await chromeAfterScroll(page);
    expect(chrome.barTop, 'the top bar must not scroll away').toBe(0);
    // The rail is sticky directly under the bar; a gap here is the black band
    // in the reporter's screenshot.
    expect(Math.abs(chrome.railTop - chrome.barBottom)).toBeLessThanOrEqual(1);
  });
}

test('the collapsed rail starts with Library, and the pin control sits at its foot', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');
  await page.addInitScript(() => localStorage.setItem('cwng:sidebar-pinned', '0'));
  await page.goto('/app/');
  await page.mouse.move(1000, 400);

  const nav = page.getByRole('navigation', { name: 'Browse' });
  await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).width)).toBe('64px');

  const geometry = await nav.evaluate((element) => {
    const navBox = element.getBoundingClientRect();
    const padTop = parseFloat(getComputedStyle(element).paddingTop);
    const links = [...element.querySelectorAll('a[href]')].map((link) => link.getBoundingClientRect());
    const pin = element.querySelector('button[aria-pressed]')!.getBoundingClientRect();
    const padBottom = parseFloat(getComputedStyle(element).paddingBottom);
    return {
      firstLinkOffset: links[0].top - navBox.top - padTop,
      pinTop: pin.top,
      // At its foot: right after the last item, or held at the bottom edge of a
      // rail long enough to scroll.
      footTop: Math.min(
        Math.max(...links.map((box) => box.bottom)),
        navBox.bottom - padBottom - pin.height,
      ),
    };
  });
  expect(geometry.firstLinkOffset, 'no empty slot above the first rail item').toBeLessThanOrEqual(4);
  // A few px of slack: the 28px button is centred in its 32px row.
  expect(geometry.pinTop).toBeGreaterThanOrEqual(geometry.footTop - 4);

  // Still reachable and working from its new place.
  await nav.hover({ position: { x: 32, y: 80 } });
  await page.getByRole('button', { name: 'Pin sidebar' }).click();
  await expect(page.getByRole('button', { name: 'Unpin sidebar' })).toHaveAttribute('aria-pressed', 'true');
});
