import { expect, test } from '@playwright/test';

/**
 * Deliberately unstubbed integration probe. cwn-local must run this branch's
 * backend: the test creates through Flask, reads through data.json, and then
 * soft-deletes its own row. It exists specifically because route.fulfill()
 * cannot prove an origin producer is wired.
 */
test('two real browser contexts persist separate non-null origin devices', async ({ browser, page }) => {
  const storageState = await page.context().storageState();
  const installationHeader = 'X-CWNG-Webreader-Installation-Id';
  const firstContext = await browser.newContext({
    storageState,
    extraHTTPHeaders: {
      [installationHeader]: '19420000-0000-4000-8000-000000000001',
    },
  });
  const secondContext = await browser.newContext({
    storageState,
    extraHTTPHeaders: {
      [installationHeader]: '19420000-0000-4000-8000-000000000002',
    },
  });
  const firstRequest = firstContext.request;
  const secondRequest = secondContext.request;
  // Ask the library which books exist rather than naming one. A hardcoded id
  // passed against a dev container carrying hundreds of books and 404'd in CI,
  // whose seed is three — the probe was coupled to one machine's data, which is
  // the opposite of what an unstubbed integration test is for.
  const catalog = await firstRequest.get('/api/v1/books?per_page=1');
  expect(catalog.ok()).toBeTruthy();
  const items = (await catalog.json() as { items?: { id: number }[] }).items ?? [];
  expect(items.length, 'library seed must contain at least one book').toBeGreaterThan(0);
  const bookId = items[0].id;
  const marker = `origin-e2e-${Date.now()}`;
  const [firstCsrfResponse, secondCsrfResponse] = await Promise.all([
    firstRequest.get('/api/v1/auth/csrf'),
    secondRequest.get('/api/v1/auth/csrf'),
  ]);
  expect(firstCsrfResponse.ok()).toBeTruthy();
  expect(secondCsrfResponse.ok()).toBeTruthy();
  const { csrf_token: firstCsrf } = await firstCsrfResponse.json() as { csrf_token: string };
  const { csrf_token: secondCsrf } = await secondCsrfResponse.json() as { csrf_token: string };
  const createdIds: { request: typeof firstRequest; csrf: string; id: string }[] = [];

  try {
    for (const [index, requestContext, csrf] of [
      [1, firstRequest, firstCsrf],
      [2, secondRequest, secondCsrf],
    ] as const) {
      const created = await requestContext.post(`/annotations/${bookId}`, {
        headers: { 'X-CSRFToken': csrf },
        data: {
          cfi_range: 'epubcfi(/6/4!/4/2,/1:0,/1:9)',
          highlighted_text: `${marker}-${index}`,
          highlight_color: 'yellow',
        },
      });
      expect(created.status()).toBe(201);
      const createdBody = await created.json() as { annotation_id: string };
      createdIds.push({ request: requestContext, csrf, id: createdBody.annotation_id });
    }

    const listed = await firstRequest.get(`/annotations/${bookId}/data.json`);
    expect(listed.ok()).toBeTruthy();
    const payload = await listed.json() as {
      annotations: { annotation_id: string; origin_device_id: string | null }[];
      devices: Record<string, { label: string; type: string }>;
    };
    const deviceListResponse = await firstRequest.get('/api/annotations/devices');
    const deviceList = await deviceListResponse.json() as {
      devices?: { kind?: string }[];
    };
    // The PR e2e lane overlays this branch's SPA on :dev's backend. `kind` was
    // added with the M1 backend, so its absence proves this probe is aimed at
    // the old server and cannot test per-browser resolution there.
    test.skip(
      payload.devices === undefined || !deviceList.devices?.some((device) => device.kind),
      'API has no M1 device-kind field — this lane runs :dev\'s backend, not this branch. '
      + 'The origin producer is covered by tests/unit/test_annotation_route_device_resolution.py '
      + 'and by running this spec against a container built from this branch.',
    );

    const rows = createdIds.map(({ id }) =>
      payload.annotations.find((annotation) => annotation.annotation_id === id));
    expect(rows.every(Boolean), 'the real data.json must return both created rows').toBeTruthy();
    for (const row of rows) {
      // toBeNull() passes on undefined, which is how an absent field slipped
      // through as "not null". Require an actual value.
      expect(row!.origin_device_id, 'origin must be populated, not absent').toBeTruthy();
      expect(payload.devices[row!.origin_device_id!]?.type).toBe('webreader');
    }
    expect(rows[0]!.origin_device_id).not.toBe(rows[1]!.origin_device_id);
  } finally {
    await Promise.all(createdIds.map(({ request: requestContext, csrf, id }) =>
      requestContext.delete(`/annotations/${bookId}/${encodeURIComponent(id)}`, {
        headers: { 'X-CSRFToken': csrf },
      })));
    await firstContext.close();
    await secondContext.close();
  }
});
