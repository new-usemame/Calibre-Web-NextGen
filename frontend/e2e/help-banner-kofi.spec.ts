import { test, expect, type Page } from '@playwright/test';
import { prioritizeAnnouncements } from '../src/lib/announcementQueue';

const LEGACY_HELP_KEY = 'cwng_help_banner_dismissed_v1';
const LEGACY_KOFI_KEY = 'cwng_kofi_banner_dismissed_v1';
const HELP_KEY = 'cwng_banner_dismissed:help-announcement-v1';
const KOFI_KEY = 'cwng_banner_dismissed:kofi-support-v1';
const SUPERSEDED_KOFI_KEY = 'cwng_banner_dismissed:kofi-support-v0';
const SUPPORT_URL = 'https://ko-fi.com/calibrewebnextgen';

test('announcement channels keep only the last-declared entry before priority sorting', () => {
  const announcements = [
    { id: 'channel-less-high', priority: 300 },
    { id: 'kofi-old', priority: 900, channel: 'kofi' },
    { id: 'channel-less-low', priority: 10 },
    { id: 'kofi-new', priority: 100, channel: 'kofi' },
  ];

  expect(prioritizeAnnouncements(announcements).map(({ id }) => id)).toEqual([
    'channel-less-high',
    'kofi-new',
    'channel-less-low',
  ]);
});

test('superseded dismissal IDs cannot pre-dismiss or revive the current channel entry', () => {
  const announcements = prioritizeAnnouncements([
    { id: 'kofi-old', priority: 900, channel: 'kofi' },
    { id: 'help', priority: 200 },
    { id: 'kofi-new', priority: 100, channel: 'kofi' },
  ]);

  const dismissed = new Set(['kofi-old']);
  expect(announcements.find(({ id }) => !dismissed.has(id))?.id).toBe('help');
  dismissed.add('help');
  expect(announcements.find(({ id }) => !dismissed.has(id))?.id).toBe('kofi-new');
  dismissed.add('kofi-new');
  expect(announcements.find(({ id }) => !dismissed.has(id))).toBeUndefined();
});

test('a reused object reference still contributes only its last channel declaration', () => {
  const reused = { id: 'kofi-reused', priority: 100, channel: 'kofi' };
  const announcements = prioritizeAnnouncements([
    reused,
    { id: 'help', priority: 200 },
    reused,
  ]);

  expect(announcements.map(({ id }) => id)).toEqual(['help', 'kofi-reused']);
});

async function resetDismissals(page: Page) {
  await page.goto('/app');
  await page.evaluate((keys) => keys.forEach((key) => localStorage.removeItem(key)), [
    LEGACY_HELP_KEY,
    LEGACY_KOFI_KEY,
    HELP_KEY,
    KOFI_KEY,
    SUPERSEDED_KOFI_KEY,
  ]);
  await page.reload();
}

async function recordWindowOpenCalls(page: Page) {
  await page.evaluate(() => {
    const browserWindow = window as Window & { __bannerOpenCalls: unknown[][] };
    browserWindow.__bannerOpenCalls = [];
    window.open = ((...args: unknown[]) => {
      browserWindow.__bannerOpenCalls.push(args);
      return null;
    }) as typeof window.open;
  });
}

test.beforeEach(async ({ page }) => resetDismissals(page));

test('highest-priority undismissed announcement shows and dismissal reveals the queue', async ({ page }) => {
  const slot = page.getByRole('status').filter({
    hasText: /Need to report an issue\? Try the new|Support us on Ko-fi!/,
  });

  await expect(slot).toHaveAttribute('data-announcement-id', 'help-announcement-v1');
  await expect(slot).toContainText('Need to report an issue? Try the new');
  await expect(page.getByRole('link', { name: /Support us on Ko-fi!.*Open Ko-fi/ })).toHaveCount(0);

  await slot.getByRole('button', { name: 'Dismiss help announcement' }).click();

  await expect(slot).toHaveAttribute('data-announcement-id', 'kofi-support-v1');
  await expect(slot).toContainText('Support us on Ko-fi!');
  expect(await page.evaluate((key) => localStorage.getItem(key), HELP_KEY)).toBe('1');
});

test('clicking the Ko-fi banner opens Ko-fi and dismisses it durably', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), HELP_KEY);
  await page.reload();
  await recordWindowOpenCalls(page);

  const supportLink = page.getByRole('link', { name: /Support us on Ko-fi!.*Open Ko-fi/ });
  await expect(supportLink).toHaveAttribute('href', SUPPORT_URL);
  await expect(supportLink).toHaveAttribute('target', '_blank');
  await expect(supportLink).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(supportLink).toContainText('Support us on Ko-fi!');
  await expect(supportLink).toContainText('Open Ko-fi →');
  const renderedStyle = await page.locator('[data-announcement-id="kofi-support-v1"]').evaluate(
    (element) => {
      const bannerRect = element.getBoundingClientRect();
      const linkRect = element.querySelector('a')!.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        bannerWidth: bannerRect.width,
        bannerHeight: bannerRect.height,
        linkWidth: linkRect.width,
        linkHeight: linkRect.height,
        background: style.backgroundImage,
        borderBottom: style.borderBottomWidth,
        boxShadow: style.boxShadow,
      };
    },
  );
  expect(renderedStyle.bannerWidth).toBe(renderedStyle.linkWidth);
  expect(renderedStyle.bannerHeight).toBeCloseTo(40, 4);
  expect(renderedStyle.linkWidth).toBe(renderedStyle.bannerWidth);
  expect(renderedStyle.linkHeight).toBeCloseTo(40, 4);
  expect(renderedStyle.background).toBe(
    'linear-gradient(90deg, rgb(7, 56, 77), rgb(8, 70, 94) 58%, rgb(6, 68, 94))',
  );
  expect(renderedStyle.borderBottom).toBe('0px');
  expect(renderedStyle.boxShadow).toBe('none');

  await supportLink.click({ position: { x: 4, y: 4 } });

  const openCalls = await page.evaluate(() =>
    (window as Window & { __bannerOpenCalls: unknown[][] }).__bannerOpenCalls,
  );
  expect(openCalls).toEqual([[SUPPORT_URL, '_blank', 'noopener,noreferrer']]);
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_KEY)).toBe('1');
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);

  await page.reload();
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
});

test('keyboard activation opens Ko-fi and moves through link then dismiss button', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), HELP_KEY);
  await page.reload();
  await recordWindowOpenCalls(page);

  const supportLink = page.getByRole('link', { name: /Support us on Ko-fi!.*Open Ko-fi/ });
  const dismissButton = page.getByRole('button', { name: 'Dismiss Ko-fi support message' });
  await supportLink.focus();
  await expect(supportLink).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dismissButton).toBeFocused();
  await supportLink.focus();
  await page.keyboard.press('Enter');

  expect(await page.evaluate(() =>
    (window as Window & { __bannerOpenCalls: unknown[][] }).__bannerOpenCalls,
  )).toEqual([[SUPPORT_URL, '_blank', 'noopener,noreferrer']]);
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
  await expect(page.locator('main#main')).toBeFocused();
});

test('middle-click opens Ko-fi and dismisses the banner', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), HELP_KEY);
  await page.reload();
  await recordWindowOpenCalls(page);

  await page.getByRole('link', { name: /Support us on Ko-fi!.*Open Ko-fi/ }).click({ button: 'middle' });

  expect(await page.evaluate(() =>
    (window as Window & { __bannerOpenCalls: unknown[][] }).__bannerOpenCalls,
  )).toEqual([[SUPPORT_URL, '_blank', 'noopener,noreferrer']]);
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_KEY)).toBe('1');
  await page.reload();
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
});

test('Ko-fi X dismisses without opening or navigating', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), HELP_KEY);
  await page.reload();
  await recordWindowOpenCalls(page);
  const startingUrl = page.url();

  await page.getByRole('button', { name: 'Dismiss Ko-fi support message' }).click();

  expect(page.url()).toBe(startingUrl);
  expect(await page.evaluate(() =>
    (window as Window & { __bannerOpenCalls: unknown[][] }).__bannerOpenCalls,
  )).toEqual([]);
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_KEY)).toBe('1');
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
});

test('keyboard dismissal of the final announcement moves focus to main', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), HELP_KEY);
  await page.reload();
  const dismissButton = page.getByRole('button', { name: 'Dismiss Ko-fi support message' });

  await dismissButton.focus();
  await page.keyboard.press('Enter');

  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
  await expect(page.locator('main#main')).toBeFocused();
});

test('legacy dismissal keys migrate independently without re-nagging existing users', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), LEGACY_HELP_KEY);
  await page.reload();

  await expect(page.locator('[data-announcement-id]')).toHaveAttribute(
    'data-announcement-id',
    'kofi-support-v1',
  );
  expect(await page.evaluate((key) => localStorage.getItem(key), HELP_KEY)).toBe('1');
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_KEY)).toBeNull();

  await resetDismissals(page);
  await page.evaluate((key) => localStorage.setItem(key, '1'), LEGACY_KOFI_KEY);
  await page.reload();

  await expect(page.locator('[data-announcement-id]')).toHaveAttribute(
    'data-announcement-id',
    'help-announcement-v1',
  );
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_KEY)).toBe('1');
  expect(await page.evaluate((key) => localStorage.getItem(key), HELP_KEY)).toBeNull();
  await page.getByRole('button', { name: 'Dismiss help announcement' }).click();
  await expect(page.locator('[data-announcement-id]')).toHaveCount(0);
});

test('slot is empty after every queued announcement is dismissed', async ({ page }) => {
  const slot = page.locator('[data-announcement-id]');
  await page.getByRole('button', { name: 'Dismiss help announcement' }).click();
  await page.getByRole('button', { name: 'Dismiss Ko-fi support message' }).click();
  await expect(slot).toHaveCount(0);
});

test('a namespaced Ko-fi dismissal alone survives cold load without hiding Help', async ({ page }) => {
  await page.evaluate((key) => localStorage.setItem(key, '1'), KOFI_KEY);
  await page.reload();

  const slot = page.locator('[data-announcement-id]');
  await expect(slot).toHaveAttribute('data-announcement-id', 'help-announcement-v1');
  await page.getByRole('button', { name: 'Dismiss help announcement' }).click();
  await expect(slot).toHaveCount(0);
});

test('a superseded Ko-fi dismissal ID does not pre-dismiss the current Ko-fi banner', async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem('cwng_banner_dismissed:kofi-support-v0', '1');
    localStorage.setItem('cwng_banner_dismissed:help-announcement-v1', '1');
  });
  await page.reload();

  await expect(page.locator('[data-announcement-id]')).toHaveAttribute(
    'data-announcement-id',
    'kofi-support-v1',
  );
});

test('Help menu ends with a secure external Ko-fi support link', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /^Help(?: — new updates available)?$/ });
  await trigger.focus();
  await page.keyboard.press('Enter');

  const panel = page.getByText('Help & support').locator('..');
  const items = panel.locator('a, button');
  const supportLink = panel.getByRole('link', { name: 'Support on Ko-fi →' });
  await expect(supportLink).toHaveAttribute('href', SUPPORT_URL);
  await expect(supportLink).toHaveAttribute('target', '_blank');
  await expect(supportLink).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(items.last()).toHaveText('Support on Ko-fi →');

  for (let index = 0; index < 6; index += 1) await page.keyboard.press('Tab');
  await expect(supportLink).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('Help menu renders the Ko-fi label from an authenticated translated catalog', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    await route.fulfill({ response, json: { ...me, locale: 'de' } });
  });
  await page.reload();

  await page.getByRole('button', { name: /^Hilfe/ }).click();
  await expect(page.getByRole('link', { name: 'Auf Ko-fi unterstützen →' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
});

test('storage failures keep the in-memory queue usable', async ({ page }) => {
  await page.addInitScript(() => {
    const originalGetItem = Storage.prototype.getItem;
    const originalSetItem = Storage.prototype.setItem;
    const isBannerKey = (key: string) =>
      key.startsWith('cwng_banner_dismissed:')
      || key === 'cwng_help_banner_dismissed_v1'
      || key === 'cwng_kofi_banner_dismissed_v1';
    Object.defineProperty(Storage.prototype, 'getItem', {
      configurable: true,
      value(this: Storage, key: string) {
        if (isBannerKey(key)) throw new DOMException('Storage disabled', 'SecurityError');
        return originalGetItem.call(this, key);
      },
    });
    Object.defineProperty(Storage.prototype, 'setItem', {
      configurable: true,
      value(this: Storage, key: string, value: string) {
        if (isBannerKey(key)) throw new DOMException('Storage disabled', 'SecurityError');
        return originalSetItem.call(this, key, value);
      },
    });
  });
  await page.reload();

  const slot = page.locator('[data-announcement-id]');
  await expect(slot).toHaveAttribute('data-announcement-id', 'help-announcement-v1');
  await page.getByRole('button', { name: 'Dismiss help announcement' }).click();
  await expect(slot).toHaveAttribute('data-announcement-id', 'kofi-support-v1');
  await page.getByRole('button', { name: 'Dismiss Ko-fi support message' }).click();
  await expect(slot).toHaveCount(0);
});
