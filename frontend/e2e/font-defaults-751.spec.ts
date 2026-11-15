import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import { randomUUID } from 'node:crypto';

test('admin UI font defaults seed new account profiles while existing profiles remain personal', async ({ page, browser }, testInfo) => {
  const desktop = { width: 1280, height: 800 };
  const mobile = { width: 375, height: 812 };
  await page.setViewportSize(desktop);
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('/app/admin');
  await expect(page.getByRole('heading', { name: 'Library settings', exact: true })).toBeVisible();

  const accountBefore = await page.request.get('/api/v1/account');
  expect(accountBefore.ok(), await accountBefore.text()).toBeTruthy();
  const existingFonts = (await accountBefore.json()) as { ui_font_body: string; ui_font_display: string };
  const configBefore = await page.request.get('/api/v1/admin/config');
  expect(configBefore.ok(), await configBefore.text()).toBeTruthy();
  const previousDefaults = (await configBefore.json()) as {
    config_default_ui_font_body: string;
    config_default_ui_font_display: string;
  };
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  expect(csrfResponse.ok(), await csrfResponse.text()).toBeTruthy();
  const csrf = ((await csrfResponse.json()) as { csrf_token: string }).csrf_token;
  const username = `font751-${randomUUID().slice(0, 12)}`;
  let userId: number | undefined;
  let context: Awaited<ReturnType<typeof browser.newContext>> | undefined;

  const settings = page.locator('#library-settings');
  try {
    // Save through the New UI, then verify the classic admin form reads it.
    await settings.getByLabel('Default UI body font for new users').selectOption('serif');
    await settings.getByLabel('Default UI display font for new users').selectOption('mono');
    await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
    await expect(settings.getByRole('status')).toContainText('Settings saved.');

    const savedConfig = await page.request.get('/api/v1/admin/config');
    expect(savedConfig.ok(), await savedConfig.text()).toBeTruthy();
    expect(await savedConfig.json()).toMatchObject({
      config_default_ui_font_body: 'serif',
      config_default_ui_font_display: 'mono',
    });
    await page.goto('/admin/viewconfig');
    await expect(page.getByLabel('Default UI body font for new users')).toHaveValue('serif');
    await expect(page.getByLabel('Default UI display font for new users')).toHaveValue('mono');

    // Save the opposite pair through the classic form, then check the persisted
    // SPA config. This exercises both real admin write paths.
    await page.locator('#config_default_ui_font_body').selectOption('mono');
    await page.locator('#config_default_ui_font_display').selectOption('serif');
    await page.locator('#config_default_ui_font_body').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath('classic-font-defaults-1280.jpg'),
      type: 'jpeg',
      quality: 75,
    });
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.locator('#config_default_ui_font_body')).toHaveValue('mono');
    await expect(page.locator('#config_default_ui_font_display')).toHaveValue('serif');
    const classicSaved = await page.request.get('/api/v1/admin/config');
    expect(classicSaved.ok(), await classicSaved.text()).toBeTruthy();
    expect(await classicSaved.json()).toMatchObject({
      config_default_ui_font_body: 'mono',
      config_default_ui_font_display: 'serif',
    });

    await page.goto('/app/admin');
    await expect(page.getByRole('heading', { name: 'Library settings', exact: true })).toBeVisible();
    const accountAfter = await page.request.get('/api/v1/account');
    expect(accountAfter.ok(), await accountAfter.text()).toBeTruthy();
    expect(await accountAfter.json()).toMatchObject(existingFonts);

    const created = await page.request.post('/api/v1/admin/users', {
      headers: { 'X-CSRFToken': csrf },
      data: { name: username, password: 'Aa7!font-default-751', roles: { viewer: true } },
    });
    expect(created.status(), await created.text()).toBe(201);
    userId = ((await created.json()) as { id: number }).id;

    context = await browser.newContext({
      baseURL: new URL(page.url()).origin,
      storageState: { cookies: [], origins: [] },
    });
    const csrfLogin = await context.request.get('/api/v1/auth/csrf');
    expect(csrfLogin.ok(), await csrfLogin.text()).toBeTruthy();
    const loginToken = ((await csrfLogin.json()) as { csrf_token: string }).csrf_token;
    const login = await context.request.post('/api/v1/auth/login', {
      headers: { 'X-CSRFToken': loginToken },
      data: { username, password: 'Aa7!font-default-751', remember: false },
    });
    expect(login.ok(), await login.text()).toBeTruthy();
    const profile = await context.request.get('/api/v1/account');
    expect(profile.ok(), await profile.text()).toBeTruthy();
    expect(await profile.json()).toMatchObject({ ui_font_body: 'mono', ui_font_display: 'serif' });

    const userPage = await context.newPage();
    for (const [viewport, colorScheme] of [
      [desktop, 'light'],
      [mobile, 'dark'],
    ] as const) {
      await userPage.setViewportSize(viewport);
      await userPage.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await userPage.goto('/app');
      await expect(userPage.locator('h1').first()).toBeVisible();
      await expect.poll(() => userPage.locator('body').evaluate((el) => getComputedStyle(el).fontFamily))
        .toContain('ui-monospace');
      await expect.poll(() => userPage.locator('h1').first().evaluate((el) => getComputedStyle(el).fontFamily))
        .toContain('Iowan Old Style');
      await userPage.goto('/app/account');
      await expect(userPage.getByLabel('UI body font')).toHaveValue('mono');
      await expect(userPage.getByLabel('UI display font')).toHaveValue('serif');
    }

    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.setViewportSize(desktop);
    await page.goto('/app/admin');
    await expect(page.getByRole('heading', { name: 'Library settings', exact: true })).toBeVisible();
    const axe = await new AxeBuilder({ page }).include('#library-settings').analyze();
    expect(axe.violations.filter(issue => ['critical', 'serious'].includes(issue.impact ?? ''))).toEqual([]);
    await settings.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath('admin-font-defaults-1280.jpg'),
      type: 'jpeg',
      quality: 75,
    });

    await page.setViewportSize(mobile);
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await page.goto('/app/admin');
    await expect(page.getByRole('heading', { name: 'Library settings', exact: true })).toBeVisible();
    await settings.scrollIntoViewIfNeeded();
    await expect(settings.getByLabel('Default UI body font for new users')).toHaveValue('mono');
    await expect(settings.getByLabel('Default UI display font for new users')).toHaveValue('serif');
    const mobileAxe = await new AxeBuilder({ page }).include('#library-settings').analyze();
    expect(mobileAxe.violations.filter(issue => ['critical', 'serious'].includes(issue.impact ?? ''))).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath('admin-font-defaults-375.jpg'),
      type: 'jpeg',
      quality: 75,
    });
  } finally {
    await context?.close();
    try {
      if (userId !== undefined) {
        const deleted = await page.request.post(`/api/v1/admin/users/${userId}/delete`, {
          headers: { 'X-CSRFToken': csrf },
        });
        expect([204, 404]).toContain(deleted.status());
      }
    } finally {
      const restored = await page.request.post('/api/v1/admin/config', {
        headers: { 'X-CSRFToken': csrf },
        data: previousDefaults,
      });
      expect(restored.status(), await restored.text()).toBe(200);
    }
  }
});
