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
 *  `/admin/acquisition` from swallowing `/admin/acquisition/connections`.
 *
 *  Unmatched paths `fallback()` rather than `continue()`. Playwright checks
 *  the most recently registered handler FIRST, so this catch-all is reached
 *  before any narrower `page.route` a test registered earlier. `continue()`
 *  sends the request straight to the network and skips those handlers;
 *  `fallback()` offers it to the next matching one and only then hits the
 *  network. With `continue()` here, the `/auth/me` override below was dead
 *  code and every test that relied on it silently tested the ungranted page. */
async function intercept(page: Page, replies: Record<string, Reply>): Promise<void> {
  await page.route(`**${V1}/**`, async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    const reply = replies[pathname];
    if (!reply) return route.fallback();
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
    // The app shell always renders one PERMANENTLY EMPTY role="alert" live
    // region (it is how announcements get spoken), so a bare alert count can
    // never be 0 and asserting that only ever failed. Assert the thing meant:
    // no alert anywhere carries any text. Deliberately not filtered on
    // "could not be loaded" — any wording of any error here is a bug.
    await expect(page.getByRole('alert').filter({ hasText: /\S/ })).toHaveCount(0);
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
  /** The page is gated on `me.acquisition_access`, which is false by default
   *  (the feature ships switched off), and `App.tsx` renders <NotFound/> when
   *  it is falsy. Grant it in the response only, so the page renders without
   *  touching any server state — these specs are about its failure handling,
   *  not the gate.
   *
   *  The path is `/api/v1/auth/me` (`lib/queries.ts`, served by
   *  `cps/api/auth.py`), not `/api/v1/me`. An earlier draft routed the latter,
   *  which matches no request at all, so the real ungranted payload was used
   *  and all three specs below drove the SPA's 404 page instead of the one
   *  under test. */
  async function grantAccessInResponse(page: Page): Promise<void> {
    await page.route(`**${V1}/auth/me`, async (route) => {
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

test.describe('find books — the browse listing is a read like any other', () => {
  /* The listing was the one read on either page still deciding for itself,
   * and it had kept two failures the shared helper exists to prevent:
   *
   *   - it drew a full "The catalog could not be read" panel ON TOP of the
   *     results it had just contradicted whenever a background refresh
   *     failed, because `isError` and `data` are both truthy in that state;
   *   - it returned an "empty page" before rendering facets or pagination,
   *     so a page with no entries but a "next" link lost the only control
   *     that could get the reader off it.
   */
  async function grantAccess(page: Page): Promise<void> {
    await page.route(`**${V1}/auth/me`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        response,
        contentType: 'application/json',
        body: JSON.stringify({ ...body, acquisition_access: true }),
      });
    });
  }

  const BOOTSTRAP = {
    connections: [{ id: 'c1', label: 'Gutenberg', adapter: 'opds', enabled: true, revision: 1 }],
    can_acquire: true,
    runtime: { available: true, reasons: [] as string[] },
  };

  const page1 = (publications: unknown[], extra: Record<string, unknown> = {}) => ({
    title: 'Gutenberg', protocol: 'opds1',
    publications, navigation: [], searches: [], groups: [], facets: [], pagination: [],
    ...extra,
  });

  const nav = (title: string, selection: string) =>
    ({ title, relations: ['next'], selection });

  const book = {
    title: 'Moby-Dick', identity: 'pub-1', authors: ['Herman Melville'],
    languages: ['en'], description: null, navigation: [],
    offers: [{ format: 'EPUB', label: null, identity: 'off-1', relation: 'download', offer_id: 'o1' }],
  };

  test('an empty page keeps the pagination that can get the reader off it', async ({ page }) => {
    await grantAccess(page);
    await intercept(page, {
      [`${V1}/acquisition`]: { body: BOOTSTRAP },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
      [`${V1}/acquisition/catalog`]: {
        body: page1([], { pagination: [nav('Next page', 'sel-next')] }),
      },
    });

    await page.goto('/app/find-books');
    await expect(page.getByText('This catalog page is empty.')).toBeVisible();
    // The payload carried a way forward. Returning early threw it away and
    // left the reader on a dead page with only the browser's back button.
    await expect(page.getByRole('button', { name: 'Next page' })).toBeVisible();
  });

  test('an empty page keeps the facets that can widen the filter', async ({ page }) => {
    await grantAccess(page);
    await intercept(page, {
      [`${V1}/acquisition`]: { body: BOOTSTRAP },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
      [`${V1}/acquisition/catalog`]: {
        body: page1([], { facets: [{ title: 'Language', navigation: [nav('English', 'sel-en')] }] }),
      },
    });

    await page.goto('/app/find-books');
    await expect(page.getByText('This catalog page is empty.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'English' })).toBeVisible();
  });

  test('a refresh that fails behind results keeps them and says they are stale', async ({ page }) => {
    await grantAccess(page);
    // The refetch is driven by a real user action, not a synthetic event:
    // the client sets `refetchOnWindowFocus: false`, so dispatching `focus`
    // proves nothing. Walking into a subsection and back re-subscribes the
    // root query, and with the default `staleTime: 0` that re-reads it while
    // still serving the cached page — exactly the state under test.
    let rootReads = 0;
    await page.route(`**${V1}/acquisition/catalog**`, async (route) => {
      const { searchParams } = new URL(route.request().url());
      if (searchParams.get('selection') === 'sel-sub') {
        return route.fulfill({
          status: 200, contentType: 'application/json', body: JSON.stringify(page1([])),
        });
      }
      rootReads += 1;
      if (rootReads === 1) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(page1([book], { navigation: [nav('Subsection', 'sel-sub')] })),
        });
      }
      return route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'source_unavailable', message: 'nope' } }),
      });
    });
    await intercept(page, {
      [`${V1}/acquisition`]: { body: BOOTSTRAP },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
    });

    await page.goto('/app/find-books');
    await expect(page.getByRole('heading', { name: 'Moby-Dick' })).toBeVisible();

    await page.getByRole('button', { name: 'Subsection' }).click();
    await expect(page.getByRole('heading', { name: 'Moby-Dick' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Top of catalog' }).click();

    const stale = page.getByRole('alert')
      .filter({ hasText: 'These results could not be refreshed' });
    await expect(stale).toBeVisible();
    // The results are still real and still on screen. The old code stacked a
    // full-page "could not be read" panel above them, which reads as a
    // contradiction: a failure notice sitting on top of a working catalog.
    await expect(page.getByRole('heading', { name: 'Moby-Dick' })).toBeVisible();
    await expect(page.getByText('The catalog could not be read')).toHaveCount(0);
  });

  test('a first read that fails with nothing behind it is still a full failure', async ({ page }) => {
    // The guard rail: softening the genuine no-data failure into a small
    // stale-warning would be its own bug.
    await grantAccess(page);
    await intercept(page, {
      [`${V1}/acquisition`]: { body: BOOTSTRAP },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
      [`${V1}/acquisition/catalog`]: { status: 502 },
    });

    await page.goto('/app/find-books');
    await expect(page.getByText('The catalog could not be read')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Back to top' })).toBeVisible();
  });

  test('a search that matched nothing blames the search, not the catalog', async ({ page }) => {
    await grantAccess(page);
    await page.route(`**${V1}/acquisition/catalog**`, async (route) => {
      const { searchParams } = new URL(route.request().url());
      const searched = searchParams.get('q') !== null;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          searched
            ? page1([])
            : page1([book], { searches: [{ title: 'Search', selection: 'sel-search' }] }),
        ),
      });
    });
    await intercept(page, {
      [`${V1}/acquisition`]: { body: BOOTSTRAP },
      [`${V1}/acquisition/jobs`]: { body: { jobs: [] } },
    });

    await page.goto('/app/find-books');
    // Scoped to the catalog's own `role="search"` form: at mobile widths the
    // app shell adds a "Search the library" button, and an unscoped
    // name: 'Search' matches both.
    const searchForm = page.getByRole('search');
    await searchForm.getByRole('searchbox', { name: 'Search this catalog' }).fill('zzzznothing');
    await searchForm.getByRole('button', { name: 'Search', exact: true }).click();

    // "This catalog page is empty." sends the reader looking for a fault in
    // a catalog that answered correctly.
    await expect(page.getByText('No books on this catalog matched that search.')).toBeVisible();
    await expect(page.getByText('This catalog page is empty.')).toHaveCount(0);
  });
});
