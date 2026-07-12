import { expect, test } from '@playwright/test';
import { DEFAULT_THEME, resolveTheme } from '../src/lib/themes';

test.describe('theme logic', () => {
  test('resolves concrete, system, empty, and unknown choices safely', () => {
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
    expect(resolveTheme('system')).toBe('dark'); // Node has no matchMedia.
    expect(resolveTheme(undefined)).toBe(DEFAULT_THEME);
    expect(resolveTheme('not-a-theme')).toBe(DEFAULT_THEME);
  });
});

test.describe('per-user theme picker', () => {
  // These cases intentionally mutate the same seeded user's persisted preference.
  test.describe.configure({ mode: 'serial' });

  test('applies light tokens immediately and keeps them after a successful save', async ({ page }) => {
    await page.goto('/app/account');
    const picker = page.getByLabel('Theme');
    const original = await picker.inputValue();

    try {
      // selectOption does not dispatch a change for an already-selected value.
      if (original === 'light') {
        const darkSave = page.waitForResponse((r) =>
          r.url().includes('/api/v1/account/profile') && r.request().method() === 'POST');
        await picker.selectOption('dark');
        expect((await darkSave).ok()).toBeTruthy();
      }

      const lightSave = page.waitForResponse((r) =>
        r.url().includes('/api/v1/account/profile') && r.request().method() === 'POST');
      await picker.selectOption('light');
      expect((await lightSave).ok()).toBeTruthy();

      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      await expect(page.locator('#acc-theme-msg, #acc-theme ~ [role="status"]')).toHaveText('Theme saved.');
      await expect.poll(() => page.evaluate(() => localStorage.getItem('cwng.theme'))).toBe('light');

      const rendered = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        const select = document.querySelector<HTMLSelectElement>('#acc-theme');
        return {
          background: root.getPropertyValue('--bg').trim(),
          text: root.getPropertyValue('--text').trim(),
          selectBackground: select ? getComputedStyle(select).backgroundColor : '',
        };
      });
      expect(rendered).toEqual({
        background: '#f4f1ea',
        text: '#23292f',
        selectBackground: 'rgb(231, 225, 212)',
      });

      await page.reload();
      await expect(page.getByLabel('Theme')).toHaveValue('light');
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

      await page.goto('/app/admin');
      const adminSelect = page.locator('main select').first();
      await expect(adminSelect).toBeVisible();
      expect(await adminSelect.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          background: style.backgroundColor,
          color: style.color,
          radius: style.borderRadius,
        };
      })).toEqual({
        background: 'rgb(244, 241, 234)',
        color: 'rgb(35, 41, 47)',
        radius: '10px',
      });

      await page.goto('/app');
      await page.locator('a[href*="/book/"]').first().click();
      const readLink = page.locator('a[href*="/read/"]').first();
      if (await readLink.isVisible().catch(() => false)) {
        await page.evaluate(() => localStorage.removeItem('cwng.reader.theme'));
        await readLink.click();
        const readerBar = page.locator('header').first();
        await expect(readerBar).toBeVisible();
        expect(await readerBar.evaluate((el) => {
          const style = getComputedStyle(el);
          return { background: style.backgroundColor, color: style.color };
        })).toEqual({
          background: 'rgba(255, 255, 255, 0.96)',
          color: 'rgb(42, 42, 42)',
        });
      }
    } finally {
      if (original !== 'light') {
        await page.goto('/app/account');
        const restore = page.waitForResponse((r) =>
          r.url().includes('/api/v1/account/profile') && r.request().method() === 'POST');
        await page.getByLabel('Theme').selectOption(original);
        expect((await restore).ok()).toBeTruthy();
      }
    }
  });

  test('rolls the preview and local cache back when persistence fails', async ({ page }) => {
    await page.route('**/api/v1/account/profile', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'save_failed', message: 'Save failed' } }),
      }),
    );

    await page.goto('/app/account');
    const picker = page.getByLabel('Theme');
    const original = await picker.inputValue();
    const attempted = original === 'light' ? 'dark' : 'light';
    await picker.selectOption(attempted);

    await expect(page.locator('#acc-theme-msg, #acc-theme ~ [role="status"]')).toHaveText('Could not save theme.');
    await expect(picker).toHaveValue(original);
    await expect(page.locator('html')).toHaveAttribute('data-theme', resolveTheme(original));
    await expect.poll(() => page.evaluate(() => localStorage.getItem('cwng.theme'))).toBe(original);
  });
});
