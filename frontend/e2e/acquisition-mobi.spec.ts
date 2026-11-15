import { expect, test } from '@playwright/test';

test('direct MOBI stays opt-in and its catalog choice survives an edit without exposing credentials', async ({ page }) => {
  const catalog = { id: 'catalog', label: 'Legal books', adapter: 'opds', enabled: true, revision: 3 };
  const mutations: { method: string; body: any }[] = [];
  await page.route('**/api/v1/admin/acquisition**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') mutations.push({ method: request.method(), body: request.postDataJSON() });
    const result = path.endsWith('/connections/catalog') && request.method() === 'GET'
      ? { ...catalog, config: { endpoint: 'https://catalog.example.invalid/opds', auth_kind: 'bearer',
          username: '', has_secret: true, allow_mobi: true } }
      : path.endsWith('/connections') && request.method() === 'GET' ? { connections: [catalog] }
        : path.endsWith('/users') ? { users: [] } : path.endsWith('/jobs') ? { jobs: [] }
          : { enabled: true, migration_status: 'ready', runtime: { available: true, reasons: [] }, ok: true };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/app/admin/acquisition');
  const choice = page.getByRole('checkbox', { name: 'Allow direct DRM-free MOBI 6 books', exact: true });
  await expect(choice).not.toBeChecked();
  await page.getByLabel('Name', { exact: true }).fill('New legal catalog');
  await page.getByLabel('Catalog address', { exact: true }).fill('https://catalog.example.invalid/new');
  await choice.focus();
  await choice.press('Space');
  await expect(choice).toBeChecked();
  await page.getByRole('button', { name: 'Add connection', exact: true }).click();
  await expect.poll(() => mutations.length).toBe(1);
  expect(mutations[0].body.config.allow_mobi).toBe(true);
  await expect(choice).not.toBeChecked();
  await page.getByRole('button', { name: 'Edit Legal books', exact: true }).click();
  await expect(choice).toBeChecked();
  await expect(page.getByLabel('Token', { exact: true })).toHaveValue('');
  await choice.focus();
  await choice.press('Space');
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect.poll(() => mutations.length).toBe(2);
  expect(mutations[1]).toMatchObject({ method: 'PATCH', body: { expected_revision: 3, config: { allow_mobi: false } } });
  expect(mutations[1].body.config).not.toHaveProperty('secret');
});
