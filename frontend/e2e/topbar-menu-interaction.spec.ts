import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { CONCRETE_THEMES } from '../src/lib/themes';

async function settleAnimations(page: import('@playwright/test').Page) {
  await expect.poll(() => page.evaluate(() =>
    document.getAnimations().filter(animation =>
      Number.isFinite(animation.effect?.getComputedTiming().endTime)
      && (animation.pending || animation.playState === 'running')).length,
  ), 'finite animations have settled before contrast inspection').toBe(0);
}

for (const menuName of ['Account', 'Help']) {
  test(`${menuName} menu pins on click and closes through normal dismissal controls`, async ({ page, isMobile, browserName }) => {
    test.setTimeout(120_000); // Five settled palettes, including focused contrast.
    await page.goto('/app');
    const trigger = page.getByRole('button', { name: menuName === 'Account' ? /^Account:/i : /^Help(?: —|$)/ });
    await expect(trigger).toBeVisible();
    if (isMobile) {
      await trigger.tap();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await trigger.tap();
      await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    } else {
      await trigger.hover();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await page.mouse.move(1, 1);
      await expect(trigger).toHaveAttribute('aria-expanded', 'false');
      await trigger.hover();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await trigger.click();
      await expect(trigger, 'clicking a hover-open menu pins it instead of closing it').toHaveAttribute('aria-expanded', 'true');
      await page.mouse.move(1, 1);
      // Cross the hook's 140ms delayed-close boundary to prove that pinning
      // survives pointer departure, rather than inspecting the old open frame.
      await page.waitForTimeout(200);
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await trigger.click();
      await expect(trigger).toHaveAttribute('aria-expanded', 'false');
      await page.mouse.move(1, 1);
    }
    await trigger.focus();
    await trigger.press('Enter');
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    // macOS Safari's default link navigation is Option-Tab; other engines
    // and Linux WebKit use Tab. Exercise the browser's native keyboard path.
    await trigger.press(browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab');
    await expect(trigger.locator('xpath=ancestor::div[1]').getByRole('link').first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await expect(trigger).toBeFocused();
    await trigger.press('Space');
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    // Keep normal motion for the interactions above; use the existing a11y
    // harness preference when comparing final palette colors.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    // Inspect visible, settled menus in every palette. During the opacity entry
    // animation axe can skip text and miss a real contrast violation.
    for (const theme of CONCRETE_THEMES) {
      await page.evaluate(slug => document.documentElement.setAttribute('data-theme', slug), theme);
      await settleAnimations(page);
      const accessibility = await new AxeBuilder({ page }).include('header').analyze();
      expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious'), theme).toEqual([]);
      if (menuName === 'Account') {
        await page.getByRole('button', { name: /^Sign out$/i }).focus();
        await settleAnimations(page);
        const focused = await new AxeBuilder({ page }).include('header').analyze();
        expect(focused.violations.filter(v => v.impact === 'critical' || v.impact === 'serious'), `${theme} focused sign out`).toEqual([]);
        await trigger.focus();
      }
    }
    await page.mouse.click(1, 1);
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });
}
