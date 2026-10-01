import { test, expect } from './fixtures';
import type { Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function token(page: Page) {
  return (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token as string;
}

async function screenshots(page: Page, info: TestInfo, state: string) {
  await page.mouse.move((page.viewportSize()?.width ?? 1280) - 20, 10);
  const nav = page.getByRole('navigation', { name: 'Browse', exact: true });
  if (info.project.name === 'desktop' && await nav.count()) {
    await expect.poll(async () => (await nav.boundingBox())?.width).toBe(64);
  }
  const original = await page.locator('html').getAttribute('data-theme');
  const themes = state.startsWith('classic-') ? ['caliblur'] : ['light', 'dark'];
  for (const theme of themes) {
    if (theme !== 'caliblur') await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      `${state} must fit the viewport`).toBe(true);
    const path = info.outputPath(`${state}-${info.project.name}-${theme}.jpg`);
    await page.screenshot({ path, type: 'jpeg', quality: 72, fullPage: true, animations: 'disabled' });
    await info.attach(`${state}-${info.project.name}-${theme}.jpg`, { path, contentType: 'image/jpeg' });
  }
  await page.evaluate((value) => { if (value) document.documentElement.dataset.theme = value; }, original);
}

// #1734: exercise the actual overview → create → manage flow as a viewer who
// cannot edit other people's public shelves. Unique account/shelves avoid races.
test('a viewer creates and manages ordinary and smart shelves without shelf-editor privileges', async ({ secondaryUser }, testInfo) => {
  const page = secondaryUser.page;
  await page.setViewportSize(testInfo.project.name === 'mobile' ? { width: 375, height: 667 } : { width: 1280, height: 800 });
  const headers = { 'X-CSRFToken': await token(page) };
  const name = `Collection ${secondaryUser.id}`;
  const smartName = `Smart ${secondaryUser.id}`;
  let ordinaryId: number | undefined;
  let smartId: number | undefined;
  try {
    await page.goto('/app/shelves');
    await page.locator('main').getByRole('link', { name: 'Create shelf', exact: true }).click();
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill(name);
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).check();
    await screenshots(page, testInfo, 'ordinary-shelf-create');
    await page.getByRole('button', { name: 'Create shelf', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
    ordinaryId = Number(page.url().match(/\/shelf\/(\d+)/)?.[1]);
    expect(ordinaryId).toBeGreaterThan(0);
    await page.goto('/app/shelves');
    await expect(page.getByRole('link', { name: `Settings for ${name}`, exact: true })).toBeVisible();
    await screenshots(page, testInfo, 'ordinary-shelf-overview');
    await page.getByRole('link', { name: `Settings for ${name}`, exact: true }).click();
    await expect(page.getByRole('checkbox', { name: 'Share with everyone', exact: true })).toBeChecked();
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/app/shelf/${ordinaryId}$`));
    expect((await (await page.request.get(`/api/v1/shelves/${ordinaryId}`)).json()).is_public).toBe(false);

    await page.goto('/app/magic');
    await expect(page.getByRole('heading', { level: 1, name: 'Smart shelves', exact: true })).toBeVisible();
    await page.locator('main').getByRole('link', { name: 'Create smart shelf', exact: true }).click();
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill(smartName);
    await page.getByRole('textbox', { name: 'Title value', exact: true }).fill('e');
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).check();
    await screenshots(page, testInfo, 'smart-shelf-create');
    await page.getByRole('button', { name: 'Create smart shelf', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toContainText(smartName);
    smartId = Number(page.url().match(/\/magic\/(\d+)/)?.[1]);
    expect(smartId).toBeGreaterThan(0);
    // No reload: the sidebar list must refresh after creating a smart shelf.
    if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    else await page.getByRole('navigation', { name: 'Browse', exact: true }).hover();
    await expect(page.getByRole('navigation', { name: 'Browse', exact: true }).getByRole('link', { name: smartName, exact: true })).toBeVisible();
    const nav = page.getByRole('navigation', { name: 'Browse', exact: true });
    const toggle = nav.getByRole('button', { name: 'Toggle smart shelf list', exact: true });
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(nav.getByRole('link', { name: smartName, exact: true })).toBeHidden();
    await page.reload();
    if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    else await nav.hover();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(nav.getByRole('link', { name: smartName, exact: true })).toBeVisible();
    await page.goto('/app/magic');
    await expect(page.getByRole('link', { name: `Settings for ${smartName}`, exact: true })).toBeVisible();
    await screenshots(page, testInfo, 'smart-shelf-overview');
    await page.getByRole('link', { name: `Settings for ${smartName}`, exact: true }).click();
    await expect(page.getByRole('checkbox', { name: 'Share with everyone', exact: true })).toBeChecked();
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/app/magic/${smartId}$`));
    expect((await (await page.request.get(`/api/v1/magicshelf/${smartId}`)).json()).is_public).toBe(false);
    await page.goto(`/shelf/edit/${ordinaryId}`);
    await expect(page.locator('input[name="is_public"]')).toBeVisible();
    await page.locator('input[name="is_public"]').scrollIntoViewIfNeeded();
    await screenshots(page, testInfo, 'classic-ordinary-shelf-settings');
    await page.goto(`/magicshelf/${smartId}/edit`);
    await expect(page.locator('#shelf-is-public')).toBeVisible();
    await page.locator('#shelf-is-public').scrollIntoViewIfNeeded();
    await screenshots(page, testInfo, 'classic-smart-shelf-settings');
  } finally {
    if (ordinaryId) expect((await page.request.post(`/api/v1/shelves/${ordinaryId}/delete`, { headers })).ok()).toBeTruthy();
    if (smartId) expect((await page.request.post(`/magicshelf/${smartId}/delete`, { headers })).ok()).toBeTruthy();
  }
});

test('hidden smart shelves remain manageable and can be restored, with accessible overview controls', async ({ page }) => {
  const headers = { 'X-CSRFToken': await token(page) };
  await page.goto('/app/magic');
  await expect(page.getByRole('heading', { level: 1, name: 'Smart shelves', exact: true })).toBeVisible();
  const list = (await (await page.request.get('/api/v1/magicshelves?manage=1')).json()).items;
  const shelf = list.find((s: { can_hide: boolean; is_hidden: boolean }) => s.can_hide && !s.is_hidden);
  expect(shelf, 'seed user should have a hideable system smart shelf').toBeTruthy();
  try {
    await page.getByRole('button', { name: `Hide ${shelf.name}`, exact: true }).click();
    await expect(page.getByRole('button', { name: `Show ${shelf.name}`, exact: true })).toBeVisible();
    const visible = (await (await page.request.get('/api/v1/magicshelves')).json()).items;
    expect(visible.map((s: { id: number }) => s.id)).not.toContain(shelf.id);
    await page.reload();
    await page.getByRole('button', { name: `Show ${shelf.name}`, exact: true }).click();
    await expect(page.getByRole('button', { name: `Hide ${shelf.name}`, exact: true })).toBeVisible();
    const violations = (await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()).violations;
    expect(violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
  } finally {
    expect((await page.request.post(`/api/v1/magicshelves/${shelf.id}/visibility`, { headers, data: { visible: true } })).ok()).toBeTruthy();
  }
});

for (const scenario of [
  { name: 'built-in shelves stay private', owner: true, system: true },
  { name: 'non-owner administrators need the public-shelf editing role to change sharing', owner: false, system: false },
]) {
  test(scenario.name, async ({ page }) => {
    const me = await (await page.request.get('/api/v1/auth/me')).json();
    await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: {
      ...me, role: { ...me.role, admin: true, edit_shelfs: false, share_shelfs: true },
    } }));
    await page.route(/\/api\/v1\/magicshelf\/1734(?:\?|$)/, (route) => route.fulfill({ json: {
      id: 1734, name: 'Visibility review fixture', icon: '🪄',
      is_system: scenario.system, is_owner: scenario.owner, can_edit: true,
      is_public: !scenario.system, kobo_sync: false, opds_expose: false,
      rules: { condition: 'AND', rules: [{ field: 'title', operator: 'contains', value: 'e' }] },
    } }));
    await page.goto('/app/magic/1734/edit');
    await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue('Visibility review fixture');
    const sharing = page.getByRole('checkbox', { name: 'Share with everyone', exact: true });
    if (scenario.system) await expect(sharing).toHaveCount(0);
    else {
      await expect(sharing).toBeChecked();
      await expect(sharing).toBeDisabled();
    }
  });
}
