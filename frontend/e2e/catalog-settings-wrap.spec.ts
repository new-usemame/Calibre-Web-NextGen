import { test, expect } from './fixtures';
import type { Locator, Page, Response } from '@playwright/test';

async function expectInsideViewport(control: Locator, page: Page) {
  await expect(control).toBeVisible();
  await expect(control).toBeInViewport({ ratio: 1 });
  const bounds = await control.boundingBox();
  expect(bounds).not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
}

function readingPreferenceWrite(response: Response, hidden: boolean) {
  if (!response.url().includes('/api/v1/account/preferences')
      || response.request().method() !== 'POST') return false;
  return response.request().postDataJSON()?.preferences?.reading_tags_hidden === hidden;
}

test('wrapped desktop View settings keeps native preferences reachable through resize', async ({
  page: adminPage, secondaryUser, browser, baseURL,
}, info) => {
  // The upload action makes this owned viewer's toolbar match the affected
  // account. Its role and preference changes disappear with fixture cleanup.
  const adminCsrf = await adminPage.request.get('/api/v1/auth/csrf').then(r => r.json());
  expect((await adminPage.request.post(`/api/v1/admin/users/${secondaryUser.id}`, {
    headers: { 'X-CSRFToken': adminCsrf.csrf_token },
    data: { roles: { upload: true } },
  })).ok()).toBeTruthy();

  const profile = info.project.use;
  const context = await browser.newContext({
    baseURL,
    viewport: profile.viewport,
    isMobile: profile.isMobile,
    hasTouch: profile.hasTouch,
    deviceScaleFactor: profile.deviceScaleFactor,
    userAgent: profile.userAgent,
    storageState: await secondaryUser.context.storageState(),
  });
  const page = await context.newPage();
  try {
    const csrf = await page.request.get('/api/v1/auth/csrf').then(r => r.json());
    expect((await page.request.post('/api/v1/account/preferences', {
      headers: { 'X-CSRFToken': csrf.csrf_token },
      data: { preferences: { reading_tags_hidden: true } },
    })).ok()).toBeTruthy();
    await page.setViewportSize({ width: 1260, height: 800 });
    await page.goto('/app');
    const gear = page.getByTestId('catalog-view-settings');
    const upload = page.getByRole('link', { name: 'Upload books', exact: true });
    await expect(upload).toBeVisible();
    await gear.click();

    const menu = page.getByTestId('catalog-view-settings-menu');
    const readingTags = page.getByTestId('show-reading-tags');
    let observedWrappedGear = false;
    for (const width of [1260, 1190, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      const uploadBounds = (await upload.boundingBox())!;
      const gearBounds = (await gear.boundingBox())!;
      observedWrappedGear ||= gearBounds.y >= uploadBounds.y + uploadBounds.height;
      await expectInsideViewport(menu, page);
      await expectInsideViewport(readingTags, page);
      await expect(readingTags).not.toBeChecked();
      const shown = page.waitForResponse(r => readingPreferenceWrite(r, false));
      // This controlled checkbox commits after its preference response;
      // assert that saved state after a normal native input click.
      await readingTags.click();
      expect((await shown).ok()).toBeTruthy();
      await expect(readingTags).toBeChecked();
      const saved = await page.request.get('/api/v1/auth/me').then(r => r.json());
      expect(saved.preferences.reading_tags_hidden).toBe(false);

      const hidden = page.waitForResponse(r => readingPreferenceWrite(r, true));
      await readingTags.click();
      expect((await hidden).ok()).toBeTruthy();
      await expect(readingTags).not.toBeChecked();
    }
    // System font metrics shift the precise wrap boundary. Require a real
    // later-row gear within this bounded sweep, rather than skipping coverage
    // when a particular platform fits the toolbar at 1260px.
    expect(observedWrappedGear).toBe(true);
    await page.reload();
    await gear.click();
    await expect(readingTags).not.toBeChecked();
  } finally {
    await context.close();
  }
});
