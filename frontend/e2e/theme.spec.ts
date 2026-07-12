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

  // Runs last: it mutates the shared seeded user's theme and restores it, so it
  // must not sit between the persistence cases above (their rollback assertions
  // are sensitive to the seeded theme they inherit).
  test('Customize edit-mode ghost buttons keep a visible border on the light sidebar', async ({ page }) => {
    // Regression: the Customize capsule + its edit-mode ghost/done buttons drew
    // their chrome entirely from white-alpha (rgba(255,255,255,…) borders/fills/
    // sheens). The sidebar surface is --surface-1 = #ffffff (Light) / #f4ecd9
    // (Sepia), so on the light-scheme themes the buttons went invisible
    // light-on-light and the Customize affordance lost its shape. Assert the
    // ghost button carries a real (opaque, non-near-white) border against the
    // light rail. Desktop only — the rail is off-canvas on mobile.
    const vp = page.viewportSize();
    test.skip(!!vp && vp.width < 768, 'The Customize rail is off-canvas on mobile.');

    await page.goto('/app/account');
    const picker = page.getByLabel('Theme');
    const original = await picker.inputValue();

    try {
      if (original !== 'light') {
        const save = page.waitForResponse((r) =>
          r.url().includes('/api/v1/account/profile') && r.request().method() === 'POST');
        await picker.selectOption('light');
        expect((await save).ok()).toBeTruthy();
      }
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

      // Enter customize/edit mode so the ghost reset/cancel buttons render.
      await page.getByRole('button', { name: 'Customize navigation' }).click();
      const ghost = page.getByRole('button', { name: 'Reset to default' });
      await expect(ghost).toBeVisible();

      const border = await ghost.evaluate((el) => getComputedStyle(el).borderTopColor);
      const m = border.match(/rgba?\(([^)]+)\)/);
      expect(m, `unexpected border-color format: ${border}`).toBeTruthy();
      const parts = m![1].split(',').map((s) => parseFloat(s.trim()));
      const [r, g, b] = parts;
      const alpha = parts.length > 3 ? parts[3] : 1;
      const avg = (r + g + b) / 3;
      // Pre-fix: rgba(255,255,255,0.14) — near-transparent AND near-white, so it
      // fails both guards. Post-fix (--border-strong, opaque dark) clears both.
      expect(alpha, `ghost border too transparent to see (${border})`).toBeGreaterThanOrEqual(0.5);
      expect(avg, `ghost border too light to be visible on the light sidebar (${border})`).toBeLessThan(200);

      // Leave edit mode without persisting any reorder.
      await page.getByRole('button', { name: 'Cancel' }).click();
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
});
