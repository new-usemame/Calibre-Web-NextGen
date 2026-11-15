import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

// Instance-wide CWA settings: run only in the serialized server-state lane.
test('folder label settings persist across New UI and Classic, retaining drafts on errors', async ({ page, secondaryUser }, testInfo) => {
  test.setTimeout(120_000);
  const endpoint = '/api/v1/admin/ingest-folder-label-settings';
  const previousResponse = await page.request.get(endpoint);
  expect(previousResponse.status(), await previousResponse.text()).toBe(200);
  const previous = await previousResponse.json() as { target: string; nested: boolean; custom_columns: { lookup: string; name: string }[] };
  const csrfResponse = await page.request.get('/api/v1/auth/csrf');
  const csrf = (await csrfResponse.json() as { csrf_token: string }).csrf_token;
  const headers = { 'X-CSRFToken': csrf };
  const accountBefore = await (await page.request.get('/api/v1/account')).json() as { theme: string };
  const section = page.locator('#ingest-folder-labels');
  try {
    await page.goto('/app/admin');
    const disabled = await page.request.put(endpoint, { headers, data: { target: 'disabled', nested: false } });
    expect(disabled.status(), await disabled.text()).toBe(200);
    for (const [width, theme] of [[1280, 'light'], [375, 'dark']] as const) {
      await page.setViewportSize({ width, height: width === 1280 ? 800 : 667 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
      const themed = await page.request.post('/api/v1/account/profile', { headers, data: { theme } });
      expect(themed.status(), await themed.text()).toBe(200);
      await page.evaluate(value => localStorage.setItem('cwng.theme', value), theme);
      await page.goto('/app/admin#ingest-folder-labels');
      await page.reload(); // The preceding direct API reset is outside SPA cache.
      await expect(section.getByLabel('Write subfolder names to')).toHaveValue('disabled');
      await expect(section.getByLabel('Include nested folders')).toBeDisabled();
      await section.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`new-default-${width}.jpg`), type: 'jpeg', quality: 75 });
      await section.getByLabel('Write subfolder names to').selectOption('tags');
      await section.getByLabel('Include nested folders').check();
      await page.route(`**${endpoint}`, route => route.request().method() === 'PUT'
        ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Temporary save failure' } }) })
        : route.continue());
      await section.getByRole('button', { name: 'Save ingest folder settings' }).click();
      await expect(section.getByText('Temporary save failure', { exact: true })).toBeVisible();
      await expect(section.getByLabel('Write subfolder names to')).toHaveValue('tags');
      await expect(section.getByLabel('Include nested folders')).toBeChecked();
      await section.getByLabel('Write subfolder names to').focus();
      await expect(section.getByLabel('Write subfolder names to')).toBeFocused();
      const axe = await new AxeBuilder({ page }).include('#ingest-folder-labels').analyze();
      expect(axe.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`new-save-error-${width}.jpg`), type: 'jpeg', quality: 75 });
      await page.unroute(`**${endpoint}`);
      await section.getByRole('button', { name: 'Save ingest folder settings' }).click();
      await expect(section.getByText('Ingest folder settings saved.', { exact: true })).toBeVisible();
      await page.reload();
      await expect(section.getByLabel('Write subfolder names to')).toHaveValue('tags');
      await expect(section.getByLabel('Include nested folders')).toBeChecked();
      const custom = previous.custom_columns[0];
      if (custom) {
        await section.getByLabel('Write subfolder names to').selectOption(custom.lookup);
        await section.getByRole('button', { name: 'Save ingest folder settings' }).click();
        await expect(section.getByText('Ingest folder settings saved.', { exact: true })).toBeVisible();
        await page.reload();
        await expect(section.getByLabel('Write subfolder names to')).toHaveValue(custom.lookup);
        await section.scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath(`new-custom-column-${width}.jpg`), type: 'jpeg', quality: 75 });
      }

      await page.goto('/cwa-settings#ingest-folder-labels');
      await expect(page.locator('#auto_ingest_folder_label_target')).toHaveValue(custom?.lookup ?? 'tags');
      await expect(page.locator('#auto_ingest_folder_label_nested')).toBeChecked();
      await section.scrollIntoViewIfNeeded();
      const classicAxe = await new AxeBuilder({ page }).include('#ingest-folder-labels').analyze();
      expect(classicAxe.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
      const heading = section.getByRole('heading', { name: 'Ingest folder labels' });
      expect(await heading.evaluate(el => {
        const box = el.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return hit === el || (hit !== null && el.contains(hit));
      })).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`classic-saved-${width}.jpg`), type: 'jpeg', quality: 75 });
      await page.locator('#auto_ingest_folder_label_target').selectOption('tags');
      await page.locator('#auto_ingest_folder_label_nested').uncheck();
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.locator('#auto_ingest_folder_label_nested')).not.toBeChecked();
      const after = await page.request.get(endpoint);
      expect(await after.json()).toMatchObject({ target: 'tags', nested: false });
      await page.goto('/app/admin#ingest-folder-labels');
      await expect(section.getByLabel('Include nested folders')).not.toBeChecked();
      const reset = await page.request.put(endpoint, { headers, data: { target: 'disabled', nested: false } });
      expect(reset.status(), await reset.text()).toBe(200);
    }
    const invalid = await page.request.put(endpoint, { headers, data: { target: '#missing', nested: true } });
    expect(invalid.status()).toBe(400);
    const kept = await page.request.get(endpoint);
    expect(await kept.json()).toMatchObject({ target: 'disabled', nested: false });
    const badCsrf = await page.request.put(endpoint, { data: { target: 'tags', nested: true } });
    expect(badCsrf.status()).toBe(400);
    const crossSite = await page.request.put(endpoint, { headers: { ...headers, Origin: 'https://untrusted.invalid' }, data: { target: 'tags', nested: true } });
    expect(crossSite.status()).toBe(403);
    const viewerGet = await secondaryUser.context.request.get(endpoint);
    expect(viewerGet.status()).toBe(403);
    const viewerCsrf = await secondaryUser.context.request.get('/api/v1/auth/csrf');
    const viewerToken = (await viewerCsrf.json() as { csrf_token: string }).csrf_token;
    const viewerPut = await secondaryUser.context.request.put(endpoint, {
      headers: { 'X-CSRFToken': viewerToken }, data: { target: 'tags', nested: true },
    });
    expect(viewerPut.status()).toBe(403);
  } finally {
    await page.unroute(`**${endpoint}`);
    const restore = await page.request.put(endpoint, { headers, data: { target: previous.target, nested: previous.nested } });
    expect(restore.status(), await restore.text()).toBe(200);
    const profileRestore = await page.request.post('/api/v1/account/profile', { headers, data: { theme: accountBefore.theme } });
    expect(profileRestore.status(), await profileRestore.text()).toBe(200);
  }
});
