import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test';

type Probe = {
  label: string;
  edit: boolean;
  upload: boolean;
  expectedApiStatus: 200 | 403;
};

type CreatedUser = {
  id: number;
  name: string;
  password: string;
};

const createdUserIds: number[] = [];

async function csrfHeaders(request: APIRequestContext): Promise<Record<string, string>> {
  const response = await request.get('/api/v1/auth/csrf');
  const body = await response.json() as { csrf_token?: string };
  expect(response.ok(), `CSRF request failed (${response.status()}): ${JSON.stringify(body)}`).toBeTruthy();
  expect(body.csrf_token, 'CSRF response did not include csrf_token').toBeTruthy();
  return { 'X-CSRFToken': body.csrf_token! };
}

async function createProbeUser(
  admin: APIRequestContext,
  probe: Probe,
  runId: string,
): Promise<CreatedUser> {
  const name = `duplicates-e2e-${probe.label}-${runId}`;
  const password = 'CWNG-duplicates-E2E-42!';
  const response = await admin.post('/api/v1/admin/users', {
    headers: await csrfHeaders(admin),
    data: {
      name,
      email: `${name}@example.test`,
      password,
      locale: 'en',
      roles: {
        viewer: true,
        edit: probe.edit,
        upload: probe.upload,
      },
    },
  });
  const body = await response.json().catch(() => null) as { id?: number } | null;
  expect(response.status(), `Could not provision ${probe.label}: ${JSON.stringify(body)}`).toBe(201);
  expect(body?.id, `Provisioned ${probe.label} response did not include an id`).toBeTruthy();
  createdUserIds.push(body!.id!);
  return { id: body!.id!, name, password };
}

async function loginThroughSpa(page: Page, user: CreatedUser): Promise<void> {
  await page.goto('/app/login');
  await page.locator('input[autocomplete="username"]').fill(user.name);
  await page.locator('input[autocomplete="current-password"]').fill(user.password);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/app\/?(?:$|\?)/, { timeout: 20_000 });
}

async function assertProbeMatchesApi(
  browser: Browser,
  baseURL: string | undefined,
  user: CreatedUser,
  probe: Probe,
): Promise<void> {
  // browser.newContext inherits the desktop project's admin storageState
  // unless it is explicitly cleared.  A probe context must start signed out
  // so the credentials below, not the shared setup session, own the page.
  const context = await browser.newContext({
    baseURL,
    storageState: { cookies: [], origins: [] },
  });
  const page = await context.newPage();

  try {
    await loginThroughSpa(page, user);

    // New users inherit the instance default sidebar bitmask.  Force the
    // Duplicates bit on so the role gate is the only variable under test, and
    // verify both the mutation response and a fresh /me read.  A missing bit
    // must fail here rather than letting both role cases pass as "hidden".
    const visibilityResponse = await page.request.post('/api/v1/account/sidebar', {
      headers: await csrfHeaders(page.request),
      data: { visibility: { duplicates: true } },
    });
    const visibilityBody = await visibilityResponse.json().catch(() => null) as {
      sidebar?: Record<string, boolean>;
    } | null;
    expect(
      visibilityResponse.ok(),
      `${probe.label}: enabling Duplicates visibility failed (${visibilityResponse.status()}): ${JSON.stringify(visibilityBody)}`,
    ).toBeTruthy();
    expect(
      visibilityBody?.sidebar?.duplicates,
      `${probe.label}: sidebar mutation succeeded but did not leave duplicates visible`,
    ).toBe(true);

    const meResponse = await page.request.get('/api/v1/auth/me');
    const me = await meResponse.json() as {
      role: Record<string, boolean>;
      sidebar?: Record<string, boolean>;
    };
    expect(meResponse.ok(), `${probe.label}: /auth/me returned ${meResponse.status()}`).toBeTruthy();
    expect(me.role.edit, `${probe.label}: edit role was not provisioned as requested`).toBe(probe.edit);
    expect(me.role.upload, `${probe.label}: upload role was not provisioned as requested`).toBe(probe.upload);
    expect(
      me.sidebar?.duplicates,
      `${probe.label}: a fresh /auth/me read says duplicates visibility is off`,
    ).toBe(true);

    const duplicatesApi = await page.request.get('/api/v1/duplicates');
    expect(
      duplicatesApi.status(),
      `${probe.label}: Duplicates API authorization changed unexpectedly`,
    ).toBe(probe.expectedApiStatus);
    const apiAllowsDuplicates = duplicatesApi.ok();

    // Reload after the out-of-band account mutation so React Query reads the
    // persisted sidebar bit.  The rendered link count must then be exactly the
    // API's authorization answer for this real user session.
    await page.reload();
    await expect(page.getByRole('navigation', { name: 'Browse' })).toBeVisible();
    const duplicatesLink = page.getByRole('link', { name: 'Duplicates', exact: true });
    await expect(
      duplicatesLink,
      `${probe.label}: GET /api/v1/duplicates returned ${duplicatesApi.status()}, so the sidebar link count must be ${apiAllowsDuplicates ? 1 : 0}`,
    ).toHaveCount(apiAllowsDuplicates ? 1 : 0);
    if (apiAllowsDuplicates) await expect(duplicatesLink).toBeVisible();
  } finally {
    await context.close();
  }
}

test.afterAll(async ({ request }) => {
  if (createdUserIds.length === 0) return;
  try {
    const headers = await csrfHeaders(request);
    for (const id of createdUserIds.reverse()) {
      const response = await request.post(`/api/v1/admin/users/${id}/delete`, { headers });
      if (!response.ok() && response.status() !== 404) {
        console.warn(`Could not clean up Duplicates e2e user ${id}: HTTP ${response.status()}`);
      }
    }
  } catch (error) {
    console.warn('Could not clean up Duplicates e2e users:', error);
  }
});

test('Duplicates sidebar authorization matches its API for edit-only and upload-only users', async ({
  page,
  browser,
  baseURL,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the role contract is viewport-independent');

  const runId = `${Date.now()}-${process.pid}-${testInfo.retry}-${Math.random().toString(36).slice(2, 8)}`;
  const probes: Probe[] = [
    { label: 'edit-only', edit: true, upload: false, expectedApiStatus: 200 },
    { label: 'upload-only', edit: false, upload: true, expectedApiStatus: 403 },
  ];

  for (const probe of probes) {
    const user = await createProbeUser(page.request, probe, runId);
    await assertProbeMatchesApi(browser, baseURL, user, probe);
  }
});
