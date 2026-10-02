import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

test('reader help follows host policy and keeps documentation and updates', async ({ secondaryUser }, info) => {
  const page = secondaryUser.page;
  await page.setViewportSize(info.project.name === 'mobile' ? { width: 375, height: 667 } : { width: 1280, height: 800 });
  let support: { show_project_links: boolean; url: string | null; label: string | null } | undefined;
  // Exercise the real menu without changing server-wide settings in parallel
  // CI. Authentication and the non-admin account remain real; only the policy
  // projection is varied. A separate private-rig flow verifies its persistence.
  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    expect(payload.role.admin).toBe(false);
    delete payload.support;
    if (support) payload.support = support;
    await route.fulfill({ response, json: payload });
  });
  for (const mode of ['default', 'host', 'none'] as const) {
    support = mode === 'default' ? undefined : {
      show_project_links: false,
      url: mode === 'host' ? 'https://example.invalid/library-help' : null,
      label: mode === 'host' ? 'Ask our library' : null,
    };
    await page.goto('/app');
    // Let the lower-priority donation announcement become eligible without
    // changing the test's account/server policy or dismissing it permanently.
    await page.evaluate(() => {
      localStorage.setItem('cwng_banner_dismissed:help-announcement-v1', '1');
      localStorage.removeItem('cwng_banner_dismissed:kofi-support-v1');
      localStorage.removeItem('cwng_kofi_banner_dismissed_v1');
    });
    await page.reload();
    await expect(page.getByRole('button', { name: `Account: ${secondaryUser.username}`, exact: true })).toBeVisible();
    const trigger = page.getByRole('button', { name: /^Help(?: —|$)/ });
    await trigger.click();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByRole('link', { name: 'Documentation', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: "What's new", exact: true })).toBeVisible();
    for (const name of ['Report Issue on GitHub', 'Report Issue on Discord', 'Ask in Discord', 'Support on Ko-fi →']) {
      const link = page.getByRole('link', { name, exact: true });
      if (mode === 'default') await expect(link).toBeVisible();
      else await expect(link).toHaveCount(0);
    }
    const localLink = page.getByRole('link', { name: 'Ask our library', exact: true });
    if (mode === 'host') {
      await expect(localLink).toHaveAttribute('href', support!.url!);
      await expect(localLink).toHaveAttribute('rel', 'noopener noreferrer');
      await trigger.press('Tab');
      await expect(page.getByRole('link', { name: "What's new", exact: true })).toBeFocused();
    } else await expect(localLink).toHaveCount(0);
    // Inspect after the account and menu have rendered their policy. An early
    // absence while /me is still loading cannot prove a hidden announcement.
    const donation = page.locator('[data-announcement-id="kofi-support-v1"]');
    if (mode === 'default') await expect(donation).toBeVisible();
    else await expect(donation).toHaveCount(0);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => document.documentElement.setAttribute('data-theme', value), theme);
      await expect.poll(() => page.evaluate(() => document.getAnimations().filter(a =>
        Number.isFinite(a.effect?.getComputedTiming().endTime) && (a.pending || a.playState === 'running')).length)).toBe(0);
      const axe = await new AxeBuilder({ page }).include('header').analyze();
      expect(axe.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
      const path = info.outputPath(`${mode}-${theme}-${info.project.name}.jpg`);
      await page.screenshot({ path, type: 'jpeg', quality: 75, animations: 'disabled' });
      await info.attach(`${mode}-${theme}`, { path, contentType: 'image/jpeg' });
    }
    await page.keyboard.press('Escape');
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await expect(trigger).toBeFocused();
  }
});

test('admin help retains project destinations under a host-only policy', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('cwng_banner_dismissed:help-announcement-v1', '1');
    localStorage.removeItem('cwng_banner_dismissed:kofi-support-v1');
    localStorage.removeItem('cwng_kofi_banner_dismissed_v1');
  });
  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    expect(payload.role.admin).toBe(true);
    payload.support = { show_project_links: false, url: 'https://example.invalid/help', label: 'Ask our library' };
    await route.fulfill({ response, json: payload });
  });
  await page.goto('/app');
  await expect(page.locator('[data-announcement-id="kofi-support-v1"]')).toBeVisible();
  await page.getByRole('button', { name: /^Help(?: —|$)/ }).click();
  await expect(page.getByRole('link', { name: 'Report Issue on GitHub', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Support on Ko-fi →', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ask our library', exact: true })).toHaveCount(0);
});
