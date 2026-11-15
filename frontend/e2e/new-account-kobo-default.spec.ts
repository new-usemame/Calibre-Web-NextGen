import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

// Create through the real admin API, then use that account's own profile and
// persisted choice. Existing seed users are never edited by this scenario.
test('new account starts shelf-only, explains first sync and can choose the whole library', async ({ secondaryUser }, info) => {
  const page = secondaryUser.page;
  await page.setViewportSize(info.project.name === 'mobile' ? { width: 375, height: 812 } : { width: 1280, height: 800 });
  await page.goto('/app/account');
  const control = page.getByRole('checkbox', { name: /Sync only selected shelves to/ });
  await expect(control).toBeChecked();
  await expect(page.locator('#acc-kobo-sync-help')).toContainText('If no books arrive');
  await expect(control).toHaveAttribute('aria-describedby', 'acc-kobo-sync-help');
  for (const theme of ['light', 'dark']) {
    const csrf = (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token;
    const update = await page.request.post('/api/v1/account/profile', { headers: { 'X-CSRFToken': csrf }, data: { theme } });
    expect(update.ok(), await update.text()).toBeTruthy();
    await page.reload();
    await expect(control).toBeChecked();
    await control.scrollIntoViewIfNeeded();
    const violations = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter(v => v.impact === 'critical' || v.impact === 'serious');
    expect(violations).toEqual([]);
    const artifact = info.outputPath(`new-account-${theme}-${info.project.name}.jpg`);
    await page.screenshot({ path: artifact, type: 'jpeg', quality: 75, animations: 'disabled' });
    await info.attach(`new-account-${theme}`, { path: artifact, contentType: 'image/jpeg' });
  }
  await control.focus();
  await page.keyboard.press('Space');
  await expect(control).not.toBeChecked();
  const [saved] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/v1/account/profile') && r.request().method() === 'POST'),
    page.getByRole('button', { name: 'Save profile', exact: true }).click(),
  ]);
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await page.reload();
  await expect(control).not.toBeChecked();
  const account = await page.request.get('/api/v1/account');
  expect(account.ok()).toBeTruthy();
  expect((await account.json()).kobo_only_shelves_sync).toBe(false);

  const me = await (await page.request.get('/api/v1/auth/me')).json();
  if (me.features?.kobo_sync) {
    await page.context().addCookies([{ name: 'cwng_prefer_spa', value: '0', url: new URL(page.url()).origin }]);
    await page.goto('/me');
    await expect(page.locator('#kobo_only_shelves_sync')).not.toBeChecked();
    await expect(page.locator('#kobo-shelf-sync-help')).toContainText('If no books arrive');
    await page.locator('#kobo_only_shelves_sync').scrollIntoViewIfNeeded();
    const artifact = info.outputPath(`classic-account-${info.project.name}.jpg`);
    await page.screenshot({ path: artifact, type: 'jpeg', quality: 75, animations: 'disabled' });
    await info.attach('classic-account', { path: artifact, contentType: 'image/jpeg' });
  }
});
