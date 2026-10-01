import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function token(page: Page) {
  return (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token as string;
}

// #1734: exercise the actual overview → create → manage flow as a viewer who
// cannot edit other people's public shelves. Unique account/shelves avoid races.
test('a viewer creates and manages ordinary and smart shelves without shelf-editor privileges', async ({ secondaryUser }, testInfo) => {
  const page = secondaryUser.page;
  const headers = { 'X-CSRFToken': await token(page) };
  const name = `Collection ${secondaryUser.id}`;
  const smartName = `Smart ${secondaryUser.id}`;
  let ordinaryId: number | undefined;
  let smartId: number | undefined;
  try {
    await page.goto('/app/shelves');
    await page.getByRole('link', { name: 'Create shelf', exact: true }).click();
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill(name);
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).check();
    await page.getByRole('button', { name: 'Create shelf', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
    ordinaryId = Number(page.url().match(/\/shelf\/(\d+)/)?.[1]);
    expect(ordinaryId).toBeGreaterThan(0);
    await page.goto('/app/shelves');
    await page.getByRole('link', { name: `Settings for ${name}`, exact: true }).click();
    await expect(page.getByRole('checkbox', { name: 'Share with everyone', exact: true })).toBeChecked();
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    expect((await (await page.request.get(`/api/v1/shelves/${ordinaryId}`)).json()).is_public).toBe(false);

    await page.goto('/app/magic');
    await expect(page.getByRole('heading', { level: 1, name: 'Smart shelves', exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'Create smart shelf', exact: true }).click();
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill(smartName);
    await page.getByRole('textbox', { name: 'Title value', exact: true }).fill('e');
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).check();
    await page.getByRole('button', { name: 'Create smart shelf', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toContainText(smartName);
    smartId = Number(page.url().match(/\/magic\/(\d+)/)?.[1]);
    expect(smartId).toBeGreaterThan(0);
    // No reload: the sidebar list must refresh after creating a smart shelf.
    if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: 'Open menu', exact: true }).click();
    else await page.getByRole('navigation', { name: 'Browse', exact: true }).hover();
    await expect(page.getByRole('navigation', { name: 'Browse', exact: true }).getByRole('link', { name: smartName, exact: true })).toBeVisible();
    await page.goto('/app/magic');
    await page.getByRole('link', { name: `Settings for ${smartName}`, exact: true }).click();
    await expect(page.getByRole('checkbox', { name: 'Share with everyone', exact: true })).toBeChecked();
    await page.getByRole('checkbox', { name: 'Share with everyone', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    expect((await (await page.request.get(`/api/v1/magicshelf/${smartId}`)).json()).is_public).toBe(false);
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
