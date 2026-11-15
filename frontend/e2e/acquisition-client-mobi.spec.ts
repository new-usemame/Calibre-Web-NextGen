import { expect, test } from '@playwright/test';

for (const adapter of ['sabnzbd', 'nzbget', 'qbittorrent', 'transmission']) {
  test(`${adapter} completed MOBI stays opt-in and roundtrips keyboard changes without credentials`, async ({ page }) => {
    const connection = { id: 'client', label: 'Legal completed books', adapter, enabled: true, revision: 3 };
    const mutations: { method: string; body: any }[] = [];
    await page.route('**/api/v1/admin/acquisition**', async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== 'GET') mutations.push({ method: request.method(), body: request.postDataJSON() });
      const result = path.endsWith('/connections/client') && request.method() === 'GET'
        ? { ...connection, config: { endpoint: 'https://client.example.invalid/api',
            auth_kind: adapter === 'sabnzbd' ? 'none' : 'basic', username: 'fixture',
            category: 'books', remote_path: '/downloads', local_path: '/completed', has_secret: true, allow_mobi: true } }
        : path.endsWith('/connections') && request.method() === 'GET' ? { connections: [connection] }
          : path.endsWith('/users') ? { users: [] } : path.endsWith('/jobs') ? { jobs: [] }
            : { enabled: true, migration_status: 'ready', runtime: { available: true, reasons: [] }, ok: true };
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
    });
    await page.goto('/app/admin/acquisition');
    await page.getByRole('combobox', { name: 'Connection type', exact: true }).selectOption(adapter);
    const choice = page.getByRole('checkbox', { name: 'Allow completed DRM-free MOBI 6 books', exact: true });
    await expect(choice).not.toBeChecked();
    const hint = await choice.getAttribute('aria-describedby');
    expect(hint).toBeTruthy();
    await expect(page.locator(`[id="${hint}"]`)).toContainText('Applies only to this download client.');
    await page.getByLabel('Name', { exact: true }).fill('New legal client');
    await page.getByLabel('API endpoint', { exact: true }).fill('https://client.example.invalid/api');
    if (adapter === 'sabnzbd') await page.getByLabel('API key', { exact: true }).fill('fixture-key');
    else {
      await page.getByLabel('Username', { exact: true }).fill('fixture');
      await page.getByLabel('Password', { exact: true }).fill('fixture-password');
    }
    await page.getByLabel('Client category or label', { exact: true }).fill('books');
    await page.getByLabel('Completed folder as the download client sees it', { exact: true }).fill('/downloads');
    await page.getByLabel('Same completed folder inside CWNG', { exact: true }).fill('/completed');
    await choice.focus();
    await choice.press('Space');
    await expect(choice).toBeChecked();
    await page.getByRole('button', { name: 'Add connection', exact: true }).click();
    await expect.poll(() => mutations.length).toBe(1);
    expect(mutations[0].body).toMatchObject({ adapter, config: { allow_mobi: true } });
    await page.getByRole('button', { name: 'Edit Legal completed books', exact: true }).click();
    await expect(choice).toBeChecked();
    await expect(page.getByLabel(adapter === 'sabnzbd' ? 'API key' : 'Password', { exact: true })).toHaveValue('');
    await choice.focus();
    await choice.press('Space');
    await page.getByRole('button', { name: 'Save connection', exact: true }).click();
    await expect.poll(() => mutations.length).toBe(2);
    expect(mutations[1]).toMatchObject({ method: 'PATCH', body: { expected_revision: 3, config: { allow_mobi: false } } });
    expect(mutations[1].body.config).not.toHaveProperty('secret');
  });
}
