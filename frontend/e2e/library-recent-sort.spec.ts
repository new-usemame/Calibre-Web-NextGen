import { test, expect, type SecondaryUserSession } from './fixtures';
import type { APIRequestContext, Page } from '@playwright/test';

/*
 * "Recent" — the Library opens on what this reader has been reading, then the
 * books they have not read in the order those were added.
 *
 * Why this is an e2e spec and not a unit test. The order itself is SQL and is
 * covered where it can be driven through every writer at once
 * (tests/unit/test_library_recent_sort.py). What only a browser can show is the
 * half that ships to the user: which sort the SPA ASKS the server for on a cold
 * load, whether the menu is showing the sort the list is actually in, and
 * whether a value left in the old storage key suppresses the new default. Each
 * of those has a mode where the server is right and the page is wrong.
 *
 * This spec also carries the first-paint claim that
 * tests/unit/test_753_default_sort_freshness.py can only pin as source text:
 * the listing request the SPA issues IS the default, so a view that opened on
 * the wrong sort would be visible here as the sort it asked for, and a
 * post-mount correction would be visible as a second listing request.
 *
 * FIXTURE — a reader of its own. Recency is per-reader state, so every test
 * here runs as a freshly created account (the `secondaryUser` fixture), which
 * is deleted again when the test ends. The shared seed login is wrong for this
 * spec in both directions: other specs WRITE that account while this one reads
 * it — default-library-view.spec.ts saves a default library filter on it, and
 * while one is saved the library grid is a filtered view, so this spec's cold
 * load sees no unfiltered listing at all (MEASURED: running those two files in
 * one desktop invocation fails this spec every time) — and this spec's own
 * writes are reading history, which is exactly the input the sort under test
 * consumes. A private account also hands the fixture a known starting point: no
 * activity anywhere, so "this book leads because it was read" cannot be left
 * over from an earlier run, and the no-activity case is itself asserted below.
 *
 * The book given reading activity is taken from the FAR END of the newest-first
 * listing — the oldest-added book in the library — so "it is first because it
 * was read" cannot be confused with "it is first because it is newest".
 * Reading is recorded through the route the web reader itself posts to.
 */

const READ_CFI = 'epubcfi(/6/14!/4/2/2[pgepubid00001]/1:0)';
const LEGACY_KEY = 'cwng:library-sort-v1';
const SORT_KEY = 'cwng:library-sort-v2';

interface BookItem { id: number; title: string }

/** The two books whose positions tell the two orders apart, for one reader. */
interface Fixture {
  page: Page;
  readBookId: number;
  readBookTitle: string;
  newestBookId: number;
}

async function csrfToken(api: APIRequestContext): Promise<string> {
  const res = await api.get('/api/v1/auth/csrf');
  expect(res.ok(), `CSRF fetch failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function listing(api: APIRequestContext, query: string): Promise<BookItem[]> {
  const res = await api.get(query);
  expect(res.ok(), `${query} failed: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { items: BookItem[] }).items;
}

/** Record reading the way the web reader does — the route it posts to. */
async function recordReading(api: APIRequestContext, id: number, percentage: number) {
  const res = await api.post(`/api/v1/books/${id}/bookmark`, {
    headers: { 'X-CSRFToken': await csrfToken(api) },
    data: { format: 'epub', bookmark: READ_CFI, percentage },
  });
  expect(res.ok(), `bookmark save for ${id} failed: ${res.status()} ${await res.text()}`)
    .toBeTruthy();
}

/**
 * Give this brand-new account one book it has been reading.
 *
 * `context.request` shares the account's cookie jar, so every call here is that
 * reader's own view of the library — not the admin's.
 */
async function aReaderWithAHistory(session: SecondaryUserSession): Promise<Fixture> {
  const api = session.context.request;
  const byDateAdded = await listing(api, '/api/v1/books?sort=new&per_page=250');
  expect(byDateAdded.length, 'the library needs at least two books to order')
    .toBeGreaterThanOrEqual(2);
  const oldest = byDateAdded[byDateAdded.length - 1];

  // Before this account has read anything, Recent IS Newest — the definition at
  // its boundary, and the control the promotion below is measured against. It
  // is also the whole library in a single page, which is where this order first
  // shipped broken: every page that reached a never-read book came back empty.
  const noHistoryYet = await listing(api, '/api/v1/books?sort=recent&per_page=250');
  expect(noHistoryYet.map((book) => book.id),
    'with no reading anywhere, Recent must be the order the books were added in')
    .toEqual(byDateAdded.map((book) => book.id));

  await recordReading(api, oldest.id, 42);
  return {
    page: session.page,
    readBookId: oldest.id,
    readBookTitle: oldest.title,
    newestBookId: byDateAdded[0].id,
  };
}

/** The ids of the cards the SPA has rendered, in DOM order. */
async function renderedIds(page: Page): Promise<number[]> {
  const hrefs = await page
    .getByTestId('catalog-grid')
    .locator('a[href*="/book/"]')
    .evaluateAll((els) => els.map((el) => el.getAttribute('href') ?? ''));
  const ids: number[] = [];
  for (const href of hrefs) {
    const match = /\/book\/(\d+)$/.exec(href);
    if (!match) continue;
    const id = Number(match[1]);
    if (ids[ids.length - 1] !== id) ids.push(id);
  }
  return ids;
}

/**
 * Load /app with a known storage state and report the sort the SPA asked for.
 *
 * The listening starts before the navigation, so the value returned is the
 * FIRST listing request of a cold load — the default itself, not whatever the
 * page settled on afterwards.
 */
async function coldLoad(page: Page, seed: Record<string, string> = {}) {
  const sorts: string[] = [];
  page.on('response', (response) => {
    if (!response.url().includes('/api/v1/books?') || response.status() !== 200) return;
    const params = new URL(response.url()).searchParams;
    // The same endpoint serves the Discover rail and the entity/read-filtered
    // views; those own `?filter=` and the entity keys and carry no sort of
    // their own. Only the plain library grid is the default under test.
    const sort = params.get('sort');
    if (sort === null || params.has('filter') || params.has('search')) return;
    if (['series', 'author', 'tag', 'publisher', 'language'].some((key) => params.has(key))) return;
    sorts.push(sort);
  });
  // Once per tab, not once per navigation: an init script runs on every
  // document, so an unguarded one would also wipe the storage a reload is
  // supposed to be reading back, and "the choice was remembered" would be
  // measuring this helper instead of the app.
  await page.addInitScript((entries: [string, string][]) => {
    try {
      if (sessionStorage.getItem('cwng-e2e-seeded') === '1') return;
      sessionStorage.setItem('cwng-e2e-seeded', '1');
      localStorage.clear();
      for (const [key, value] of entries) localStorage.setItem(key, value);
    } catch { /* storage can be disabled */ }
  }, Object.entries(seed));
  await page.goto('/app');
  await expect(page.getByRole('combobox', { name: 'Sort order' })).toBeVisible();
  await expect.poll(() => sorts.length, { message: 'no listing request observed' })
    .toBeGreaterThan(0);
  return sorts;
}

/*
 * Full page, because the claim needs both halves in one frame: the sort control
 * says "Recent" near the top and the grid it produced starts below the Discover
 * rail, which is further down than one 800px viewport reaches. The rail is
 * dismissible, but dismissing it writes a preference on the account.
 *
 * Written to the run's own output directory and attached to the report, the way
 * the other specs that carry visual evidence do it (admin-device-cards,
 * admin-context-sidebar). Not behind a flag: evidence that only exists when
 * someone remembers to set a variable is absent exactly when it is wanted, and
 * an absent image and a correct one look identical in a passing run.
 *
 * The file is named after the project, so the desktop and phone-sized runs of
 * the same test keep their own images instead of overwriting each other.
 */
async function shoot(page: Page, name: string) {
  // Park the pointer over the header first. The desktop nav rail expands on
  // hover and Playwright leaves the pointer at (0, 0), which is inside it, so
  // an unmoved mouse puts the expanded panel on top of the first column of the
  // grid — the column holding the book the shot exists to show leading.
  // `animations: 'disabled'` then runs the rail's width transition out to its
  // end, so the shot is the collapsed rail rather than a frame of it closing.
  const view = page.viewportSize();
  if (view) await page.mouse.move(view.width / 2, 8);
  const info = test.info();
  const file = `${info.project.name}-${name}.jpg`;
  const path = info.outputPath(file);
  await page.screenshot({
    path,
    type: 'jpeg',
    quality: 70,
    fullPage: true,
    animations: 'disabled',
  });
  await info.attach(file, { path, contentType: 'image/jpeg' });
}

test.describe('Recent library sort', () => {
  test('a cold load asks for Recent and shows the recently read book first', async ({ secondaryUser }) => {
    const { page, readBookId, readBookTitle, newestBookId } =
      await aReaderWithAHistory(secondaryUser);
    const sorts = await coldLoad(page);

    expect(sorts[0], 'the Library must open on Recent').toBe('recent');
    expect(sorts.filter((sort) => sort !== 'recent'),
      'a post-mount correction would show up as a listing request for another sort')
      .toEqual([]);

    const menu = page.getByRole('combobox', { name: 'Sort order' });
    await expect(menu, 'the menu must show the sort the list is actually in')
      .toHaveValue('recent');
    expect(await menu.locator('option').first().getAttribute('value'),
      'Recent must be the first entry in the menu').toBe('recent');
    await expect(menu.locator('option[value="recent"]')).toHaveText('Recent');

    // The control has to be reachable and legible at this project's viewport,
    // not merely present: #288 shipped a sort dropdown that overflowed the
    // viewport at 375px, which is where the mobile project runs this.
    const box = await menu.boundingBox();
    expect(box, 'the sort control must be laid out').toBeTruthy();
    const width = page.viewportSize()?.width ?? 0;
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);

    await expect(page.getByTestId('catalog-grid')).toBeVisible();
    await expect.poll(async () => (await renderedIds(page))[0],
      { message: `the recently read book (${readBookTitle}) must lead the grid` })
      .toBe(readBookId);
    // The id above comes from the card's href; this is the text the reader
    // actually sees. The catalog renders its cards through a measured row
    // window, so a card that carried the right link over another book's title
    // would satisfy the first assertion and fail this one.
    await expect(page.getByTestId('catalog-grid').locator('a[href*="/book/"]').first())
      .toContainText(readBookTitle);
    // The instrument check, in the browser: this book leads because it was
    // read, and it is the very last book the other order would show.
    expect(readBookId).not.toBe(newestBookId);

    await shoot(page, 'recent-default');
  });

  test('switching to Newest puts the newest book back on top', async ({ secondaryUser }) => {
    const { page, newestBookId } = await aReaderWithAHistory(secondaryUser);
    await coldLoad(page);
    const menu = page.getByRole('combobox', { name: 'Sort order' });

    await menu.selectOption('new');
    await expect(menu).toHaveValue('new');
    await expect.poll(async () => (await renderedIds(page))[0])
      .toBe(newestBookId);

    // The choice is a choice now, so it is written where the reader made it.
    expect(await page.evaluate((key) => localStorage.getItem(key), SORT_KEY)).toBe('new');
    await shoot(page, 'newest-comparison');

    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Sort order' })).toHaveValue('new');
  });

  test('a sort the reader picked before this change still wins', async ({ secondaryUser }) => {
    // The old key recorded what the page was showing, not what anyone chose —
    // but a value it could not have seeded itself with can only be a choice.
    const sorts = await coldLoad(secondaryUser.page, { [LEGACY_KEY]: 'authaz' });

    expect(sorts[0]).toBe('authaz');
    await expect(secondaryUser.page.getByRole('combobox', { name: 'Sort order' }))
      .toHaveValue('authaz');
  });

  test('the value the old key seeded itself with does not suppress Recent', async ({ secondaryUser }) => {
    // Every install that ever opened the Library holds this, whether or not
    // anyone chose it. Reading it as a choice would mean Recent reached nobody.
    const sorts = await coldLoad(secondaryUser.page, { [LEGACY_KEY]: 'new' });

    expect(sorts[0]).toBe('recent');
    await expect(secondaryUser.page.getByRole('combobox', { name: 'Sort order' }))
      .toHaveValue('recent');
  });

  test('the Global Library offers Recent without opening on it', async ({ page }) => {
    // The one test here that stays on the seed admin: browsing the global
    // library is a role the per-test account is not given. It reads a MENU
    // rather than a grid, so nothing another spec writes to that shared account
    // can change the answer.
    //
    // Skip on what the ACCOUNT is, never on whether the control turned up: "the
    // menu is missing" is a failure this test exists to catch, so it must not
    // also be its reason to stop looking. The page redirects to the library for
    // a monolibrary account (GlobalLibrary.tsx) — on such an instance there is
    // no global library to assert about, and the server half is covered in
    // tests/unit/test_library_recent_sort.py.
    const me = await (await page.request.get('/api/v1/auth/me')).json();
    test.skip(!me?.role?.browse_global, 'this account cannot browse the global library');
    test.skip(me?.library_mode === 'monolibrary',
      'this instance has no separate global library');

    await page.goto('/app/global');
    await expect(page).toHaveURL(/\/app\/global$/);
    const menu = page.getByRole('combobox', { name: 'Sort order' });
    await expect(menu, 'the global library keeps opening on what is newly available')
      .toHaveValue('new');
    expect(await menu.locator('option').first().getAttribute('value')).toBe('recent');
    await shoot(page, 'global-library-menu');
  });
});
