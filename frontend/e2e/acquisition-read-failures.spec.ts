import { test, expect, type Page, type Route } from '@playwright/test';

/*
 * The acquisition pages used to infer facts about the world from the ABSENCE
 * of data. Every read drew its body from the row count, and a failed read has
 * a row count of zero, so:
 *
 *   - a catalog list that 502'd said "No catalogs yet.",
 *   - an activity list that failed said "Nothing requested yet.",
 *   - a failed approval queue removed the whole section, so nobody was told
 *     that people were waiting,
 *   - a settings read that failed produced a page on which the feature reads
 *     as off and every switch is disabled — pixel-identical to a healthy
 *     server with the feature deliberately off.
 *
 * The unit tests in unit/acquisitionViewState.test.ts pin the decision. These
 * pin that the PAGES ask it: a green decision module wired into a page that
 * still counts rows itself would leave every symptom above in place.
 *
 * Everything here is a route interception, so no server state is touched and
 * the specs are safe in the broad lane. They run at desktop and mobile widths
 * because that is how the projects are configured.
 */

const V1 = '/api/v1';

interface Reply { status?: number; body?: unknown; delayMs?: number }

/** Answer specific `/api/v1` paths and let everything else reach the server.
 *  Dispatching on the exact pathname rather than on globs keeps
 *  `/admin/acquisition` from swallowing `/admin/acquisition/connections`. */
async function intercept(page: Page, replies: Record<string, Reply>): Promise<void> {
  await page.route(`**${V1}/**`, async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    const reply = replies[pathname];
    if (!reply) return route.continue();
    if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
    const status = reply.status ?? 200;
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(
        reply.body ?? { error: { code: 'source_unavailable', message: 'nope' } },
      ),
    });
  });
}

const READY = {
  enabled: true,
  migration_status: 'ready',
  runtime: { available: true, reasons: [] as string[] },
};

const connection = (id: string, label: string, enabled: boolean) =>
  ({ id, label, adapter: 'opds', enabled, revision: 1 });

const ADMIN = {
  settings: `${V1}/admin/acquisition`,
  connections: `${V1}/admin/acquisition/connections`,
  users: `${V1}/admin/acquisition/users`,
  jobs: `${V1}/admin/acquisition/jobs`,
};

async function openAdmin(page: Page): Promise<void> {
  await page.goto('/app/admin/acquisition');
  await expect(page.getByRole('heading', { name: 'Book sources', level: 1 })).toBeVisible();
}

test.describe('acquisition admin — a failed read is not an empty one', () => {
  test('a catalog list that fails says so, and never says "No catalogs yet."', async ({ page }) => {
    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: { status: 502 },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { body: { jobs: [] } },
    });
    await openAdmin(page);

    const failure = page.getByRole('alert').filter({ hasText: 'The catalogs could not be loaded.' });
    await expect(failure).toBeVisible();
    // The whole point: the reassuring sentence must be absent. An
    // administrator who reads it adds the catalog they already have.
    await expect(page.getByText('No catalogs yet.')).toHaveCount(0);
    await expect(failure.getByRole('button', { name: 'Try again' })).toBeEnabled();
  });

  test('a settings read that fails does not render as a healthy switched-off server', async ({ page }) => {
    await intercept(page, { [ADMIN.settings]: { status: 503 } });
    await openAdmin(page);

    await expect(
      page.getByRole('alert').filter({ hasText: 'could not be loaded' }),
    ).toBeVisible();
    // Previously this page drew a complete, calm, entirely wrong UI from
    // `undefined`: feature off, every control disabled, no explanation.
    await expect(page.getByRole('checkbox', { name: /Allow requests from book sources/ }))
      .toHaveCount(0);
  });

  test('an approval queue that fails keeps its section, so waiting requests are not hidden', async ({ page }) => {
    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: { body: { connections: [] } },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { status: 502 },
    });
    await openAdmin(page);

    // The old condition was "more than zero rows", and a failure has zero
    // rows — the section vanished entirely and the administrator had no way
    // to know anyone was waiting.
    await expect(page.getByRole('heading', { name: 'Waiting for approval' })).toBeVisible();
    await expect(
      page.getByRole('alert').filter({ hasText: 'The approval queue could not be loaded' }),
    ).toBeVisible();
  });

  test('a genuinely empty catalog list still says it is empty', async ({ page }) => {
    // The guard rail for the change above: suppressing the empty state would
    // be its own bug.
    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: { body: { connections: [] } },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { body: { jobs: [] } },
    });
    await openAdmin(page);

    await expect(page.getByText('No catalogs yet.')).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    // An empty queue stays hidden — it is not news.
    await expect(page.getByRole('heading', { name: 'Waiting for approval' })).toHaveCount(0);
  });

  test('a settings refresh that fails behind a working page leaves the page standing', async ({ page }) => {
    // The counterpart to the full-page error above, and the reason `hasData`
    // is read off the payload rather than off `isSuccess`: query-core flips
    // status to 'error' on a BACKGROUND failure while keeping the previous
    // data, so keying the teardown off `isSuccess` would demolish a working
    // page over one transient refresh.
    await intercept(page, {
      [ADMIN.connections]: { body: { connections: [] } },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { body: { jobs: [] } },
    });

    let settingsReads = 0;
    await page.route(`**${ADMIN.settings}`, async (route) => {
      if (route.request().method() !== 'GET') {
        // The PATCH fails, which is what makes the page re-read its settings.
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'acquisition_unavailable', message: 'nope' } }),
        });
      }
      settingsReads += 1;
      if (settingsReads === 1) {
        return route.fulfill({
          status: 200, contentType: 'application/json', body: JSON.stringify(READY),
        });
      }
      return route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'acquisition_unavailable', message: 'nope' } }),
      });
    });

    await openAdmin(page);
    const feature = page.getByRole('checkbox', { name: /Allow requests from book sources/ });
    await expect(feature).toBeVisible();

    await feature.click();

    // The failed write reports, the failed re-read reports — and the page the
    // administrator was using is still there underneath both.
    await expect(
      page.getByRole('alert').filter({ hasText: 'could not be refreshed' }),
    ).toBeVisible();
    await expect(feature).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Catalogs' })).toBeVisible();
  });
});

test.describe('acquisition admin — the approval queue does not libel the requester', () => {
  test('a live account is never labelled removed while the name directory is in flight', async ({ page }) => {
    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: { body: { connections: [] } },
      // The queue answers at once; the directory that holds display names
      // takes its time. That race is the bug.
      [ADMIN.jobs]: {
        body: {
          jobs: [{
            id: 'job-1', connection_id: 'c1', state: 'awaiting_approval', owner_id: 42,
            add_to_my_library: true, cancel_requested: false, error_code: null,
            claim_count: 0, title: 'Frankenstein',
          }],
        },
      },
      [ADMIN.users]: {
        delayMs: 2000,
        body: { users: [{ id: 42, name: 'maggie', access: true, auto_approve: false }] },
      },
    });
    await openAdmin(page);

    const row = page.getByRole('listitem').filter({ hasText: 'Frankenstein' });
    await expect(row).toBeVisible();
    // Before the grants call lands the page must not assert anything about
    // this person. Saying "removed" is a claim the administrator may act on.
    await expect(row).not.toContainText('Requested by a removed account');
    await expect(row).toContainText('Looking up who asked');

    // …and once it lands, they are named.
    await expect(row).toContainText('Requested by maggie', { timeout: 10_000 });
  });

  test('an account that really is gone is still reported as gone', async ({ page }) => {
    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: { body: { connections: [] } },
      [ADMIN.jobs]: {
        body: {
          jobs: [{
            id: 'job-2', connection_id: 'c1', state: 'awaiting_approval', owner_id: 99,
            add_to_my_library: false, cancel_requested: false, error_code: null,
            claim_count: 0, title: 'Dracula',
          }],
        },
      },
      [ADMIN.users]: { body: { users: [{ id: 1, name: 'admin', access: true, auto_approve: true }] } },
    });
    await openAdmin(page);

    await expect(page.getByRole('listitem').filter({ hasText: 'Dracula' }))
      .toContainText('Requested by a removed account');
  });
});

test.describe('acquisition admin — controls stay usable', () => {
  test('an enabled catalog can still be withdrawn while the migration needs review', async ({ page }) => {
    await intercept(page, {
      [ADMIN.settings]: {
        body: { ...READY, enabled: true, migration_status: 'needs_review' },
      },
      [ADMIN.connections]: {
        body: { connections: [connection('c-on', 'Live catalog', true), connection('c-off', 'Parked catalog', false)] },
      },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { body: { jobs: [] } },
    });
    await openAdmin(page);

    const live = page.getByRole('listitem').filter({ hasText: 'Live catalog' })
      .getByRole('checkbox', { name: 'Available to users' });
    const parked = page.getByRole('listitem').filter({ hasText: 'Parked catalog' })
      .getByRole('checkbox', { name: 'Available to users' });

    // The server refuses only the ENABLE direction. Taking the off switch away
    // in a degraded state removes a containment control exactly when an
    // administrator is most likely to reach for it.
    await expect(live).toBeEnabled();
    // Turning one ON would be refused with needs_review, so it stays blocked.
    await expect(parked).toBeDisabled();
  });

  test('testing one catalog does not disable the other catalogs Test buttons', async ({ page }) => {
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => { release = resolve; });

    await intercept(page, {
      [ADMIN.settings]: { body: READY },
      [ADMIN.connections]: {
        body: { connections: [connection('c-a', 'Alpha catalog', false), connection('c-b', 'Beta catalog', false)] },
      },
      [ADMIN.users]: { body: { users: [] } },
      [ADMIN.jobs]: { body: { jobs: [] } },
    });
    // Hold Alpha's probe open so the in-flight state is observable.
    await page.route(`**${V1}/admin/acquisition/connections/c-a/probe`, async (route) => {
      await held;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          title: 'Alpha', protocol: 'opds', browse: true,
          search_advertised: true, direct_download_advertised: true,
        }),
      });
    });
    await openAdmin(page);

    const alpha = page.getByRole('listitem').filter({ hasText: 'Alpha catalog' })
      .getByRole('button', { name: 'Test connection' });
    const beta = page.getByRole('listitem').filter({ hasText: 'Beta catalog' })
      .getByRole('button', { name: 'Test connection' });

    await alpha.click();
    await expect(alpha).toBeDisabled();
    // One mutation hook serves every row, so `isPending` alone took the
    // button away from every catalog at once.
    await expect(beta).toBeEnabled();

    release?.();
    await expect(alpha).toBeEnabled();
  });
});

test.describe('find books — a failed activity read is not an empty one', () => {
  /** The page is gated on `me.acquisition_access`, which is false by default.
   *  Grant it in the response only, so the page renders without touching any
   *  server state — these specs are about its failure handling, not the gate. */
  async function grantAccessInResponse(page: Page): Promise<void> {
    await page.route(`**${V1}/me`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        response,
        contentType: 'application/json',
        body: JSON.stringify({ ...body, acquisition_access: true }),
      });
    });
  }

  test('a failed jobs read tells the user their requests are not lost', async ({ page }) => {
    await grantAccessInResponse(page);
    await intercept(page, {
      [`${V1}/acquisition`]: {
        body: { connections: [], can_acquire: false, runtime: { available: true, reasons: [] } },
      },
      [`${V1}/acquisition/jobs`]: { status: 502 },
    });

    await page.goto('/app/find-books');
    await expect(page.getByRole('heading', { name: 'Find books', level: 1 })).toBeVisible();

    await expect(
      page.getByRole('alert').filter({ hasText: 'Your requests could not be loaded' }),
    ).toBeVisible();
    // "Nothing requested yet." invites the user to ask a second time. The
    // idempotency key is per page load, so a re-ask after a reload really
    // does queue a duplicate job.
    await expect(page.getByText('Nothing requested yet.')).toHaveCount(0);
  });

  test('a genuinely empty activity list still says nothing was requested', async ({ page }) => {
    await grantAccessInResponse(page);
    await intercept(page, {
      [`${V1}/acquisition`]: {
        body: { connections: [], can_acquire: false, runtime: { available: true, reasons: [] } },
      },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
    });

    await page.goto('/app/find-books');
    await expect(page.getByText('Nothing requested yet.')).toBeVisible();
  });

  test('the paused-runtime reasons are sentences, in a real list', async ({ page }) => {
    await grantAccessInResponse(page);
    await intercept(page, {
      [`${V1}/acquisition`]: {
        body: {
          connections: [], can_acquire: false,
          runtime: { available: false, reasons: ['ingest_unwritable', 'scheduler_unavailable'] },
        },
      },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
    });

    await page.goto('/app/find-books');
    const reasons = page.getByRole('list').filter({ hasText: 'The ingest folder is not writable.' });
    // role="list" is explicit in the markup because the global reset's
    // list-style:none makes Safari/VoiceOver stop treating it as a list.
    await expect(reasons).toBeVisible();
    await expect(reasons.getByRole('listitem')).toHaveCount(2);
    await expect(reasons).toContainText('The background scheduler is not running.');
    // The raw machine code must not reach the screen.
    await expect(page.getByText('ingest_unwritable')).toHaveCount(0);
  });
});
