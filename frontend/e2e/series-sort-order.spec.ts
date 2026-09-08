import {
  test,
  expect,
  request as playwrightRequest,
  type APIRequestContext,
  type Page,
} from '@playwright/test';
import { adminCredentialsFromEnvironment } from './direct-admin-api';

/*
 * Fork #573 — the new UI's series view had no way to order books by their
 * metadata series position, and it opened newest-first, so a series read
 * "12, 3, 7, 1" instead of "1, 2, 3". The position was also invisible on the
 * card unless the author had duplicated it in the title.
 *
 * This spec REPLACES the frontend source-text pins that used to stand in for
 * that behaviour in tests/unit/test_573_series_number_sort.py. Those pins
 * asserted that Catalog.tsx contained the strings `sortOptions.map(`,
 * `defaultSort = isSeries ? 'seriesasc' : 'new'` and `showSeriesIndex={isSeries}`.
 * A string cannot tell a removed feature from a renamed variable: on PR #2115
 * the contributor built a SUPERSET list and rendered `activeSortOptions.map(`,
 * every one of these behaviours still worked, and the pin reported that series
 * sorting had disappeared. `~/.claude/TESTING-STRATEGY.md` forbids source-text
 * pins for exactly this reason.
 *
 * The two assertions in that file which are NOT source pins are kept there:
 * `test_backend_sort_map_has_series_index` (imports SORT_MAP and asserts real
 * values) and `test_series_sort_msgids_anchored` (guards a toolchain fact —
 * babel does not scan .tsx, so an unanchored SPA msgid is stripped by the
 * translation job; its subject is spa_strings.py, not a .tsx spelling).
 *
 * FIXTURE. The probe series is built here rather than assumed, because a series
 * whose ascending order happens to equal the library's newest-first order makes
 * "opens in series order" and "opens newest-first" the same list — the spec
 * would pass against a broken default and prove nothing. The books are given
 * descending series positions relative to the library's own newest-first
 * listing, and `beforeAll` asserts that the two orders really do differ before
 * any test runs. Everything is restored in `afterAll`, the same contract
 * rtl-auto-direction.spec.ts uses for the book it renames.
 */

// Sorts AFTER "E2E Fixture Series": series-on-cards.spec.ts opens whichever
// series `/api/v1/series` lists first, and a probe series that displaced the
// seeded one would silently move that spec onto this fixture.
const SERIES_NAME = 'Series-order probe (#573)';
const STORAGE = 'e2e/.auth/state.json';

interface Meta {
  title: string;
  authors: string;
  series: string | null;
  series_index: number | null;
}

interface BookItem {
  id: number;
  title: string;
}

async function csrfToken(api: APIRequestContext): Promise<string> {
  const res = await api.get('/api/v1/auth/csrf');
  expect(res.ok(), `CSRF fetch failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function readMeta(api: APIRequestContext, id: number): Promise<Meta> {
  const res = await api.get(`/api/v1/books/${id}/metadata`);
  expect(res.ok(), `metadata read for ${id} failed: ${res.status()}`).toBeTruthy();
  const m = (await res.json()) as Record<string, unknown>;
  return {
    title: String(m.title ?? ''),
    authors: String(m.authors ?? ''),
    series: (m.series as string | null) ?? null,
    series_index: (m.series_index as number | null) ?? null,
  };
}

async function writeMeta(api: APIRequestContext, id: number, patch: Record<string, unknown>) {
  const res = await api.post(`/api/v1/books/${id}/metadata`, {
    headers: { 'X-CSRFToken': await csrfToken(api) },
    data: patch,
  });
  expect(res.ok(), `metadata write for ${id} failed: ${res.status()} ${await res.text()}`)
    .toBeTruthy();
}

/** Book ids in the order the API itself returns them for `query`. */
async function orderedIds(api: APIRequestContext, query: string): Promise<number[]> {
  const res = await api.get(query);
  expect(res.ok(), `${query} failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { items: BookItem[] }).items.map((b) => b.id);
}

/** The ids of the book cards the SPA has actually rendered, in DOM order. */
async function renderedIds(page: Page): Promise<number[]> {
  const hrefs = await page
    .getByTestId('catalog-grid')
    .locator('a[href*="/book/"]')
    .evaluateAll((els) => els.map((el) => el.getAttribute('href') ?? ''));
  const ids: number[] = [];
  for (const href of hrefs) {
    // `/book/5/edit` and other sub-routes must not count as a card: only the
    // card's own link ends at the id.
    const match = /\/book\/(\d+)$/.exec(href);
    if (!match) continue;
    const id = Number(match[1]);
    if (ids[ids.length - 1] !== id) ids.push(id);
  }
  return ids;
}

let api: APIRequestContext;
let seriesId: number;
/** Probe books in ASCENDING series position — position 1 first. */
let ascendingIds: number[] = [];
/** The same books as the library's own newest-first listing orders them. */
let newestFirstIds: number[] = [];
const originals = new Map<number, Meta>();

test.beforeAll(async ({ baseURL }) => {
  if (!baseURL) throw new Error('series-sort-order requires Playwright use.baseURL');
  const { username, password } = adminCredentialsFromEnvironment();
  // A page-independent context: seeding and restoration must not depend on a
  // browser page that a failing assertion may already have torn down.
  api = await playwrightRequest.newContext({ baseURL, storageState: STORAGE });
  const login = await api.post('/api/v1/auth/login', {
    headers: { 'X-CSRFToken': await csrfToken(api) },
    data: { username, password, remember: false },
  });
  expect(login.ok(), `fixture login failed: ${await login.text()}`).toBeTruthy();

  // Take the books at the FAR END of the library's default (newest-first)
  // listing. The other specs that rewrite book metadata (rtl-auto-direction,
  // edit-rating) all take `per_page=1`, i.e. the newest book, so this end of
  // the library is the one they never touch. In CI that end holds the PDF and
  // comic fixtures, deliberately backdated by the seed to keep them out of
  // everyone's way; both are found by FORMAT, so a series on them is invisible
  // to their specs.
  const listing = await orderedIds(api, '/api/v1/books?per_page=250');
  expect(listing.length, 'the library needs at least two books to order').toBeGreaterThanOrEqual(2);
  newestFirstIds = listing.slice(-3);

  for (const id of newestFirstIds) originals.set(id, await readMeta(api, id));

  // Position N counts DOWN the newest-first list, so ascending series order is
  // the exact reverse of newest-first however the server breaks timestamp ties.
  const count = newestFirstIds.length;
  for (const [offset, id] of newestFirstIds.entries()) {
    await writeMeta(api, id, { series: SERIES_NAME, series_index: count - offset });
  }
  ascendingIds = [...newestFirstIds].reverse();

  const series = await api.get('/api/v1/series');
  expect(series.ok()).toBeTruthy();
  const found = ((await series.json()) as { items: { id: number; name: string }[] }).items
    .find((s) => s.name === SERIES_NAME);
  expect(found, `probe series "${SERIES_NAME}" was not created`).toBeTruthy();
  seriesId = found!.id;

  // The instrument check. If the seed ever produced a series whose ascending
  // order matched the library's newest-first order, every ordering assertion
  // below would pass against a series view that ignored series order entirely.
  expect(
    await orderedIds(api, `/api/v1/books?series=${seriesId}&sort=new&per_page=50`),
    'fixture is not discriminating: newest-first and series-ascending are the same list',
  ).not.toEqual(ascendingIds);
});

test.afterAll(async () => {
  if (!api) return;
  for (const [id, meta] of originals) {
    // A run killed mid-flight leaves the probe series behind; restoring to a
    // value we read as the probe would make it permanent, so clear it instead.
    const wasProbe = meta.series === SERIES_NAME;
    await writeMeta(api, id, {
      series: wasProbe ? null : meta.series,
      series_index: wasProbe ? null : meta.series_index,
    }).catch(() => undefined);
  }
  await api.dispose().catch(() => undefined);
});

// Serial: every test here reads the one shared probe series, and the reverse
// test drives the sort control. Parallel workers would race on the selection.
test.describe.configure({ mode: 'serial' });

test.describe('#573 series order', () => {
  test('a series view offers both series-order options and opens ascending', async ({ page }) => {
    // The request the SPA issues IS the default: a series view that opened
    // newest-first would ask the server for sort=new. Capture it before the
    // navigation so the very first listing request is the one measured.
    const firstListing = page.waitForResponse(
      (r) => r.url().includes('/api/v1/books?')
        && new URL(r.url()).searchParams.get('series') === String(seriesId)
        && r.status() === 200,
    );
    await page.goto(`/app/series/${seriesId}`);
    const requested = new URL((await firstListing).url()).searchParams.get('sort');
    expect(requested, 'a series view must open in ascending series order (#573)').toBe('seriesasc');

    const sortControl = page.getByRole('combobox', { name: 'Sort order' });
    await expect(sortControl).toBeVisible();
    await expect(sortControl).toHaveValue('seriesasc');

    // Both options are offered, by the label a user reads and the value the
    // server understands.
    const options = await sortControl.locator('option').evaluateAll((els) =>
      els.map((el) => ({
        value: (el as HTMLOptionElement).value,
        label: (el.textContent ?? '').trim(),
      })),
    );
    expect(options).toContainEqual({ value: 'seriesasc', label: 'Series order' });
    expect(options).toContainEqual({ value: 'seriesdesc', label: 'Series order (reverse)' });
    // The base library orders survive alongside them — the series options are
    // an addition, not a replacement.
    expect(options.map((o) => o.value)).toEqual(expect.arrayContaining(['new', 'abc']));

    // And the list the user actually sees is in ascending position order,
    // which beforeAll proved is NOT the newest-first order.
    await expect
      .poll(() => renderedIds(page), { message: 'series view is not in ascending series order' })
      .toEqual(ascendingIds);
  });

  test('choosing the reverse option re-orders the list', async ({ page }) => {
    await page.goto(`/app/series/${seriesId}`);
    await expect.poll(() => renderedIds(page)).toEqual(ascendingIds);

    const reversed = page.waitForResponse(
      (r) => r.url().includes('/api/v1/books?')
        && new URL(r.url()).searchParams.get('sort') === 'seriesdesc'
        && r.status() === 200,
    );
    await page.getByRole('combobox', { name: 'Sort order' }).selectOption('seriesdesc');
    await reversed;

    await expect
      .poll(() => renderedIds(page), { message: 'the reverse option did not re-order the list' })
      .toEqual([...ascendingIds].reverse());
  });

  test('cards in a series view show the series position, and only there', async ({ page }) => {
    await page.goto(`/app/series/${seriesId}`);
    await expect.poll(() => renderedIds(page)).toEqual(ascendingIds);

    // One badge per card, reading 1, 2, 3 down the list.
    for (const [offset, id] of ascendingIds.entries()) {
      const card = page.getByTestId('catalog-grid').locator(`a[href$="/book/${id}"]`).first();
      await expect(card).toBeVisible();
      // Exact: accessible-name matching is substring-based by default, so
      // "Series position 1" would also match a card reading #10 or #1.5.
      const badge = card.getByRole('img', { name: `Series position ${offset + 1}`, exact: true });
      await expect(badge, `book ${id} shows no series position badge`).toBeVisible();
      await expect(badge).toHaveText(`#${offset + 1}`);
    }

    // The badge sits over the cover art, so an unstyled one is unreadable
    // rather than merely plain. Its own rule is what gives it a solid ground.
    const firstBadge = page.getByRole('img', { name: 'Series position 1', exact: true }).first();
    const background = await firstBadge.evaluate((el) => getComputedStyle(el).backgroundColor);
    const alpha = /rgba?\([^)]*?(?:,\s*([\d.]+))?\)$/.exec(background)?.[1];
    expect(
      background !== 'transparent' && background !== 'rgba(0, 0, 0, 0)' && alpha !== '0',
      `series position badge has no background (${background}) — it would be unreadable over cover art`,
    ).toBe(true);

    // "…and only there": the same books in the plain library carry the series
    // as a text line under the cover, never the position badge.
    await page.goto('/app/');
    await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]').first()).toBeVisible();
    await expect(page.getByRole('img', { name: /^Series position / })).toHaveCount(0);
  });

  test('a remembered library sort does not leak into a series view', async ({ page }) => {
    // #640 persists the plain library's sort across reloads. #573's default has
    // to win inside a series anyway — a remembered "Title A–Z" must not decide
    // how a series reads. This is the half of the old pin that guarded the
    // interaction between the two features.
    await page.goto('/app/');
    const librarySort = page.getByRole('combobox', { name: 'Sort order' });
    await expect(librarySort).toBeVisible();
    await librarySort.selectOption('abc');
    await expect(librarySort).toHaveValue('abc');

    const firstListing = page.waitForResponse(
      (r) => r.url().includes('/api/v1/books?')
        && new URL(r.url()).searchParams.get('series') === String(seriesId)
        && r.status() === 200,
    );
    await page.goto(`/app/series/${seriesId}`);
    expect(
      new URL((await firstListing).url()).searchParams.get('sort'),
      'the remembered library sort leaked into the series view',
    ).toBe('seriesasc');
    await expect(page.getByRole('combobox', { name: 'Sort order' })).toHaveValue('seriesasc');

    // And the library keeps its own remembered choice — the series view must
    // not have overwritten the persisted key on its way through.
    await page.goto('/app/');
    await expect(page.getByRole('combobox', { name: 'Sort order' })).toHaveValue('abc');
  });
});
