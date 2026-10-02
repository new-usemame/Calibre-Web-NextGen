import { expect, test } from '@playwright/test';

const settings = { enabled: true, migration_status: 'ready', runtime: { available: true, reasons: [] } };
const client = { id: 'client', label: 'Home SAB', adapter: 'sabnzbd', enabled: true, revision: 1 };
const source = { id: 'source', label: 'Home Prowlarr', adapter: 'newznab', enabled: true, revision: 1 };

test('indexer setup binds a client, keeps credentials private during edit, and confirms deletion', async ({ page }) => {
  let rows = [client, source];
  const mutations: { method: string; path: string; body: unknown }[] = [];
  await page.route('**/api/v1/admin/acquisition**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const body = req.postData() ? req.postDataJSON() : undefined;
    if (req.method() !== 'GET') mutations.push({ method: req.method(), path, body });
    let result: unknown;
    if (path.endsWith('/connections/source') && req.method() === 'GET') {
      result = { ...source, config: { endpoint: 'http://prowlarr:9696/1/api', auth_kind: 'none', username: '',
        has_secret: true, secret: undefined, private_origins: ['http://prowlarr:9696'],
        category: '7020', client_id: 'client', preset: 'prowlarr', download_origins: [] } };
    } else if (path.endsWith('/connections/source') && req.method() === 'DELETE') {
      rows = [client]; result = { ok: true };
    } else if (path.endsWith('/connections') && req.method() === 'GET') result = { connections: rows };
    else if (path.endsWith('/users')) result = { users: [] };
    else if (path.endsWith('/jobs')) result = { jobs: [] };
    else if (req.method() === 'GET') result = settings;
    else result = { ok: true };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/app/admin/acquisition');
  await page.getByRole('button', { name: 'Edit Home Prowlarr' }).click();
  await expect(page.getByRole('heading', { name: 'Edit connection' })).toBeFocused();
  await expect(page.getByLabel('API key', { exact: true })).toHaveValue('');
  await page.getByLabel('Name', { exact: true }).fill('Renamed');
  const origins = page.getByLabel(/^Additional local download origins/);
  await origins.fill('http://one:8090, http://two:8090');
  await expect(origins).toHaveValue('http://one:8090, http://two:8090');
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Add a connection' })).toBeVisible();
  const patch = mutations.find((row) => row.method === 'PATCH')!;
  expect(patch.path).toBe('/api/v1/admin/acquisition/connections/source');
  expect(patch.body).toMatchObject({ expected_revision: 1, label: 'Renamed', config: { client_id: 'client', preset: 'prowlarr', download_origins: ['http://one:8090', 'http://two:8090'] } });
  expect((patch.body as { config: object }).config).not.toHaveProperty('secret');
  expect((patch.body as { config: object }).config).not.toHaveProperty('allow_private_network');
  await page.getByRole('button', { name: 'Delete Home Prowlarr' }).click();
  expect(mutations.filter((row) => row.method === 'DELETE')).toHaveLength(0);
  await page.getByRole('button', { name: 'Confirm deletion' }).click();
  await expect(page.getByText('Home Prowlarr', { exact: true })).toHaveCount(0);
});

test('rejection remains explicit and a failed rejection stays visible', async ({ page }) => {
  await page.route('**/api/v1/admin/acquisition**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/reject')) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'conflict' } }) });
    const body = path.endsWith('/connections') ? { connections: [] }
      : path.endsWith('/users') ? { users: [{ id: 2, name: 'Reader', access: true, auto_approve: false }] }
        : path.endsWith('/jobs') ? { jobs: [{ id: 'pending', owner_id: 2, title: 'Requested release', state: 'awaiting_approval' }] }
          : settings;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/app/admin/acquisition');
  await page.getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'could not be rejected' })).toBeVisible();
  await expect(page.getByRole('listitem').filter({ hasText: 'Requested release' })).toBeVisible();
});

test('an ungranted acquisition deep link offers a working library link without a classic dead end', async ({ page }) => {
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, acquisition_access: false } });
  });
  await page.goto('/app/find-books');
  await expect(page.getByRole('link', { name: 'Go to your library' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open the classic interface' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Go to your library' }).click();
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
});

test('a stale connection edit keeps the form and asks for reloading instead of silently saving', async ({ page }) => {
  await page.route('**/api/v1/admin/acquisition**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/connections/source') && route.request().method() === 'PATCH') {
      expect(route.request().postDataJSON()).toHaveProperty('expected_revision', 1);
      return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'connection_changed' } }) });
    }
    const body = path.endsWith('/connections/source') ? { ...source, config: { endpoint: 'http://prowlarr:9696/1/api', auth_kind: 'none', category: '7020', client_id: 'client', preset: 'prowlarr' } }
      : path.endsWith('/connections') ? { connections: [client, source] }
        : path.endsWith('/users') ? { users: [] } : path.endsWith('/jobs') ? { jobs: [] } : settings;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/app/admin/acquisition');
  await page.getByRole('button', { name: 'Edit Home Prowlarr' }).click();
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Reload its settings before saving.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Edit connection' })).toBeVisible();
});

test('changing an indexer server asks for credential reentry and preserves the form', async ({ page }) => {
  let saved = false;
  await page.route('**/api/v1/admin/acquisition**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/connections/source') && route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON();
      if (!body.config.secret) return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: { code: 'credential_required_for_new_origin' } }) });
      expect(body.config.secret).toBe('reentered-test-key');
      expect(body.config.endpoint).toBe('http://replacement:9696/1/api');
      saved = true;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true }) });
    }
    const body = path.endsWith('/connections/source') ? { ...source, config: { endpoint: 'http://prowlarr:9696/1/api', auth_kind: 'none', category: '7020', client_id: 'client', preset: 'prowlarr', has_secret: true } }
      : path.endsWith('/connections') ? { connections: [client, source] }
        : path.endsWith('/users') ? { users: [] } : path.endsWith('/jobs') ? { jobs: [] } : settings;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/app/admin/acquisition');
  await page.getByRole('button', { name: 'Edit Home Prowlarr' }).click();
  await page.getByLabel('API endpoint', { exact: true }).fill('http://replacement:9696/1/api');
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Re-enter the credential' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Edit connection' })).toBeVisible();
  await page.getByLabel('API key', { exact: true }).fill('reentered-test-key');
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Add a connection' })).toBeVisible();
  expect(saved).toBe(true);
});

for (const adapter of ['nzbget', 'qbittorrent', 'transmission']) {
  test(`${adapter} setup submits password, category and completed mapping`, async ({ page }) => {
    const writes: { adapter: string; config: Record<string, unknown> }[] = [];
    await page.route('**/api/v1/admin/acquisition**', async (route) => {
      const req = route.request(); const path = new URL(req.url()).pathname;
      if (req.method() === 'POST' && path.endsWith('/connections')) {
        writes.push(req.postDataJSON());
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'created' }) });
      }
      const value = path.endsWith('/connections') ? { connections: [] }
        : path.endsWith('/users') ? { users: [] } : path.endsWith('/jobs') ? { jobs: [] } : settings;
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    await page.goto('/app/admin/acquisition');
    await page.getByRole('combobox', { name: 'Connection type', exact: true }).selectOption(adapter);
    await page.getByLabel('Name', { exact: true }).fill('Own client');
    await page.getByLabel('API endpoint', { exact: true }).fill('http://client:8080/');
    await page.getByLabel('Username', { exact: true }).fill('fixture');
    await page.getByLabel('Password', { exact: true }).fill('fixture-password');
    await page.getByLabel('Client category or label', { exact: true }).fill('books');
    await page.getByLabel('Completed folder as the download client sees it', { exact: true }).fill('/downloads');
    await page.getByLabel('Same completed folder inside CWNG', { exact: true }).fill('/completed');
    await page.getByRole('button', { name: 'Add connection', exact: true }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toMatchObject({ adapter, config: { auth_kind: 'basic', username: 'fixture', secret: 'fixture-password', category: 'books', remote_path: '/downloads', local_path: '/completed' } });
  });
}
