import { test, expect, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow, assertNoPageErrors, collectPageErrors } from './utils';

/*
 * Connecting a KOReader device from the e-readers page: approving the code the
 * device shows, and downloading the ready-made plugin.
 *
 * The page, its routing and the account session are the real ones; the three
 * KOReader calls are answered here so no real device or pairing state is
 * needed (the server half is covered by tests/unit/test_koreader_pairing.py
 * and test_koreader_setup_bundle.py). `/pair` itself is the real server
 * redirect.
 */

const SERVER = 'https://books.example.test';
const CODE = 'K7M4QX2P';

const minutesFromNow = (minutes: number) =>
  new Date(Date.now() + minutes * 60_000).toISOString().replace(/\.\d+Z$/, 'Z');

/** The waiting device as the server describes it: asked 3 minutes ago. */
function waitingRequest(status = 'pending') {
  return {
    user_code: 'K7M4-QX2P',
    device_name: 'Kindle Kids',
    requested_at: minutesFromNow(-3),
    expires_at: minutesFromNow(7),
    ip: '192.168.1.23',
    status,
  };
}

type Calls = { lookups: string[]; answers: string[]; bundles: Array<Record<string, unknown>> };

async function stubPage(page: Page, { koreader = true, serverUrl = SERVER } = {}): Promise<Calls> {
  const calls: Calls = { lookups: [], answers: [], bundles: [] };
  await page.route('**/api/v1/auth/me', async (route) => {
    try {
      const response = await route.fetch();
      const me = await response.json();
      await route.fulfill({ response, json: {
        ...me,
        features: { ...(me.features ?? {}), kobo_sync: false, koreader_sync: koreader },
      } });
    } catch {
      // The page closed while this was in flight: nothing is waiting for it.
    }
  });
  await page.route('**/api/v1/account/kobo-sync-token', (route) => route.fulfill({ json: {
    user_id: 1, configured: false, sync_url: null, server_url: serverUrl, is_localhost: false,
  } }));
  await page.route('**/api/annotations/devices?*', (route) => route.fulfill({ json: {
    devices: [], limit: 100, offset: 0, total: 0,
  } }));
  await page.route((url) => url.pathname.includes('/api/v1/devices/koreader/pair/'), async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    // The server takes a code with or without its dash; record which code.
    const code = decodeURIComponent(path.split('/pair/')[1].split('/')[0]).replace(/-/g, '');
    if (route.request().method() === 'GET') {
      calls.lookups.push(code);
      if (code !== CODE) {
        await route.fulfill({ status: 404, json: { error: {
          code: 'not_found', message: 'No device is waiting with this code',
        } } });
        return;
      }
      await route.fulfill({ json: waitingRequest() });
      return;
    }
    const verdict = path.endsWith('/approve') ? 'approved' : 'denied';
    calls.answers.push(`${code}:${verdict}`);
    await route.fulfill({ json: waitingRequest(verdict) });
  });
  await page.route('**/api/v1/devices/koreader/setup-bundle', async (route) => {
    calls.bundles.push(route.request().postDataJSON() ?? {});
    await route.fulfill({
      status: 200,
      body: Buffer.from('PK\u0005\u0006' + '\u0000'.repeat(18), 'binary'),
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="cwngsync-ready-made.zip"',
        'Cache-Control': 'no-store',
      },
    });
  });
  return calls;
}

function pairing(page: Page) {
  return page.getByRole('region', { name: 'Pair a Kobo or KOReader' });
}

test('the short address from the e-reader opens the approval card, and only a click approves', async ({ page }) => {
  const errors = collectPageErrors(page);
  const calls = await stubPage(page);

  // What the phone opens after scanning the device's QR code.
  await page.goto(`/pair?code=${CODE.toLowerCase()}`);
  await expect(page).toHaveURL(/\/app\/account\/devices\?pair=1&code=K7M4QX2P$/);

  const card = page.getByRole('group', { name: 'Kindle Kids wants to connect to your account.' });
  await expect(card).toBeVisible();
  await expect(card).toContainText('Asked 3 minutes ago from 192.168.1.23.');
  await expect(card).toContainText('Approve only if this is your e-reader.');
  await expect(card).toBeFocused();
  await expect(page.getByLabel('Code on the e-reader')).toHaveValue('K7M4-QX2P');
  expect(calls.lookups).toEqual([CODE]);
  // Looking a scanned code up is not answering it.
  expect(calls.answers).toEqual([]);

  const axe = await new AxeBuilder({ page }).include('#kobo-pairing')
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(axe.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
  await assertNoHorizontalOverflow(page);

  await card.getByRole('button', { name: 'Approve' }).click();
  // The card and the pressed button are gone: focus goes to the result, which
  // is how a screen reader hears it, instead of falling back to the page.
  await expect(pairing(page).getByText('Approved. Kindle Kids is finishing setup; its library appears in a few seconds.'))
    .toBeFocused();
  expect(calls.answers).toEqual([`${CODE}:approved`]);
  await expect(card).toHaveCount(0);
  // A reload must not look the answered code up again.
  await expect(page).toHaveURL(/\/app\/account\/devices$/);
  assertNoPageErrors(errors);
});

test('a code found before the page settles keeps focus on who is asking', async ({ page }) => {
  // A slow phone can take longer to paint its first frame than a nearby
  // server takes to answer the lookup. Opening the page must not then pull
  // focus back to the code box, away from the card a screen reader just read.
  await page.addInitScript(() => {
    const nextFrame = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (callback: FrameRequestCallback) =>
      window.setTimeout(() => nextFrame(callback), 400);
  });
  await stubPage(page);
  await page.goto(`/app/account/devices?pair=1&code=${CODE}`);
  const card = page.getByRole('group', { name: 'Kindle Kids wants to connect to your account.' });
  await expect(card).toBeFocused();
  await page.waitForTimeout(600);
  await expect(card).toBeFocused();
});

test('a typed code is checked before it is sent, and a wrong one is explained', async ({ page }) => {
  const calls = await stubPage(page);
  await page.goto('/app/account/devices?pair=1');
  const box = page.getByLabel('Code on the e-reader');
  await expect(box).toBeFocused();

  // Typing is forgiving about case and the dash, and shows the device's form.
  await box.pressSequentially('k7m4q');
  await expect(box).toHaveValue('K7M4-Q');
  const problem = page.locator('#koreader-code-error');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(problem).toHaveText('Enter the 8 letters and digits the e-reader shows, like K7M4-QX2P.');
  await expect(box).toHaveAttribute('aria-describedby', 'koreader-code-error');
  expect(calls.lookups).toEqual([]);

  await box.fill('BBBB-CCCC');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(problem)
    .toHaveText('No e-reader is waiting with this code. Check it, or get a new code on the e-reader.');
  expect(calls.lookups).toEqual(['BBBBCCCC']);

  await box.fill('k7m4qx2p');
  await page.keyboard.press('Enter');
  const card = page.getByRole('group', { name: 'Kindle Kids wants to connect to your account.' });
  await card.getByRole('button', { name: 'Deny' }).click();
  await expect(pairing(page).getByText('Declined. Kindle Kids was not connected.')).toBeFocused();
  expect(calls.answers).toEqual([`${CODE}:denied`]);
});

test('an answer the server refuses is explained, and the code box is ready again', async ({ page }) => {
  await stubPage(page);
  // Someone answered this code from another tab a moment earlier.
  await page.route((url) => url.pathname.endsWith('/approve'), (route) => route.fulfill({
    status: 409, json: { error: { code: 'already_decided', message: 'This code has already been approved or declined.' } },
  }));
  await page.goto(`/app/account/devices?pair=1&code=${CODE}`);
  const card = page.getByRole('group', { name: 'Kindle Kids wants to connect to your account.' });
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect(page.locator('#koreader-code-error')).toHaveText('This code has already been approved or declined.');
  await expect(card).toHaveCount(0);
  await expect(page.getByLabel('Code on the e-reader')).toBeFocused();
});

test('the ready-made plugin downloads for the address shown, which can be changed', async ({ page }) => {
  const calls = await stubPage(page, { serverUrl: 'http://localhost:8083' });
  await page.goto('/app/account/devices');
  const section = pairing(page);

  // This computer's own address cannot be reached from the e-reader.
  await expect(section.getByText('http://localhost:8083', { exact: true })).toBeVisible();
  await expect(section.getByText(/which the e-reader cannot reach/)).toBeVisible();

  await section.getByRole('button', { name: 'Change' }).click();
  const address = section.getByRole('textbox', { name: 'Your e-reader will connect to' });
  await expect(address).toBeFocused();
  await address.fill('http://192.168.1.20:8083');
  await section.getByRole('button', { name: 'Done' }).click();
  await expect(section.getByText('http://192.168.1.20:8083', { exact: true })).toBeVisible();
  await expect(section.getByText(/which the e-reader cannot reach/)).toHaveCount(0);

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    section.getByRole('button', { name: 'Download ready-made plugin' }).click(),
  ]);
  expect(download.suggestedFilename()).toBe('cwngsync-ready-made.zip');
  expect(calls.bundles).toEqual([{ server: 'http://192.168.1.20:8083' }]);
  await expect(section.getByRole('status').filter({ hasText: 'Downloaded' })).toContainText(
    'delete the zip once the plugin is on the e-reader');
  await assertNoHorizontalOverflow(page);
});

test('with KOReader sync switched off the page says so and offers nothing to press', async ({ page }) => {
  await stubPage(page, { koreader: false });
  await page.goto('/app/account/devices?pair=1&code=K7M4QX2P');
  const section = pairing(page);
  await expect(section.getByRole('status').filter({ hasText: 'KOReader sync' }))
    .toHaveText('KOReader sync is not enabled on this server.');
  await expect(section.getByRole('button', { name: 'Download ready-made plugin' })).toHaveCount(0);
  await expect(section.getByLabel('Code on the e-reader')).toHaveCount(0);
});

test('a phone that is not signed in signs in first and still lands on the code', async ({ browser, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one real password sign-in covers the redirect');
  const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  try {
    const page = await context.newPage();
    const calls = await stubPage(page);
    await page.goto(`/pair?code=${CODE}`);
    await expect(page).toHaveURL(/\/app\/login\?next=/);
    await page.locator('input[autocomplete="username"]').fill(process.env.E2E_USER || 'admin');
    await page.locator('input[autocomplete="current-password"]').fill(process.env.E2E_PASS || 'admin123');
    await page.getByRole('button', { name: /sign in/i }).click();

    await expect(page).toHaveURL(/\/app\/account\/devices\?pair=1&code=K7M4QX2P$/);
    await expect(page.getByRole('group', { name: 'Kindle Kids wants to connect to your account.' }))
      .toBeVisible();
    expect(calls.answers).toEqual([]);
  } finally {
    await context.close();
  }
});
