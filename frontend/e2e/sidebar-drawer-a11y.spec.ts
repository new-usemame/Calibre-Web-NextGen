import { expect, Locator, test } from '@playwright/test';

const DRAWER_PROJECTS = new Set(['mobile', 'ipad-touch']);
const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

type Boundary = 'first' | 'last';

async function focusLastTabStopBeforeDrawer(nav: Locator): Promise<boolean> {
  return nav.evaluate((node, selector) => {
    const candidates = Array.from(document.querySelectorAll<HTMLElement>(selector));
    const firstDrawerIndex = candidates.findIndex((candidate) => node.contains(candidate));
    const preceding = candidates.slice(0, firstDrawerIndex).filter((candidate) => {
      const style = getComputedStyle(candidate);
      return candidate.tabIndex >= 0
        && !candidate.hasAttribute('disabled')
        && !candidate.closest('[inert]')
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && candidate.getClientRects().length > 0;
    });
    const target = preceding[preceding.length - 1];
    target?.focus();
    return !!target && document.activeElement === target;
  }, FOCUSABLE_SELECTOR);
}

async function focusDrawerBoundary(nav: Locator, boundary: Boundary): Promise<number> {
  return nav.evaluate((node, { selector, boundary: edge }) => {
    const focusables = Array.from(node.querySelectorAll<HTMLElement>(selector)).filter((candidate) => {
      const style = getComputedStyle(candidate);
      return candidate.tabIndex >= 0
        && !candidate.hasAttribute('disabled')
        && candidate.getAttribute('aria-hidden') !== 'true'
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && candidate.getClientRects().length > 0;
    });
    const target = edge === 'first' ? focusables[0] : focusables[focusables.length - 1];
    target?.focus();
    return focusables.length;
  }, { selector: FOCUSABLE_SELECTOR, boundary });
}

async function isDrawerBoundaryFocused(nav: Locator, boundary: Boundary): Promise<boolean> {
  return nav.evaluate((node, { selector, boundary: edge }) => {
    const focusables = Array.from(node.querySelectorAll<HTMLElement>(selector)).filter((candidate) => {
      const style = getComputedStyle(candidate);
      return candidate.tabIndex >= 0
        && !candidate.hasAttribute('disabled')
        && candidate.getAttribute('aria-hidden') !== 'true'
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && candidate.getClientRects().length > 0;
    });
    const expected = edge === 'first' ? focusables[0] : focusables[focusables.length - 1];
    return document.activeElement === expected;
  }, { selector: FOCUSABLE_SELECTOR, boundary });
}

test('drawer is inert when closed, traps Tab when open, and Escape restores its trigger', async ({ page }, testInfo) => {
  test.skip(!DRAWER_PROJECTS.has(testInfo.project.name), 'off-canvas drawer projects only');

  await page.goto('/app');
  await page.locator('a[href*="/book/"]').first().waitFor({ state: 'visible' });

  const nav = page.locator('nav[aria-label]').first();
  const trigger = page.getByRole('banner').getByRole('button').first();

  // The closed off-canvas drawer is absent from sequential keyboard navigation.
  await expect(nav).toHaveAttribute('inert', '');
  expect(await focusLastTabStopBeforeDrawer(nav), 'a rendered tab stop precedes the drawer').toBeTruthy();
  await page.keyboard.press('Tab');
  expect(
    await nav.evaluate((node) => node.contains(document.activeElement)),
    'Tab skips every closed-drawer control',
  ).toBeFalsy();

  await trigger.click();
  await expect(nav).not.toHaveAttribute('inert', '');
  await expect.poll(
    () => nav.evaluate((node) => node.contains(document.activeElement)),
    { message: 'opening the drawer moves focus inside it' },
  ).toBeTruthy();

  const focusableCount = await focusDrawerBoundary(nav, 'last');
  expect(focusableCount, 'drawer has multiple controls to trap').toBeGreaterThan(1);
  await page.keyboard.press('Tab');
  expect(await isDrawerBoundaryFocused(nav, 'first'), 'Tab wraps last → first').toBeTruthy();

  await page.keyboard.press('Shift+Tab');
  expect(await isDrawerBoundaryFocused(nav, 'last'), 'Shift+Tab wraps first → last').toBeTruthy();

  await page.keyboard.press('Escape');
  await expect(nav).toHaveAttribute('inert', '');
  await expect(trigger).toBeFocused();
});
