import { test, expect, Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** A real EPUB, so "the reader booted" is proven by epub.js actually rendering
 *  a book rather than by it reaching an error state. Resolved from this file
 *  (the package is ESM — no __dirname) so it does not depend on the cwd. */
const EPUB_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/alice_in_wonderland.epub');

/* Fork #1074 — a guest could not open a book.
 *
 * With anonymous browsing on, the reader asks for a bookmark and for saved
 * reader settings. Both endpoints answer 401 for an anonymous user *by design*
 * (a guest has neither), and the SPA then confirmed with /api/v1/auth/me, saw
 * `role.anonymous`, and concluded the session had ended — so opening any book
 * navigated the guest to /logout and dumped them back on the home page.
 *
 * The distinction the SPA was missing: a session can only be *lost* if it once
 * existed. A visitor who never signed in is in their normal state.
 *
 * The mocks here reproduce the exact contract observed against a real container
 * (guest /me → 200 anonymous; /bookmark and /reader/settings → 401), so the test
 * pins the client's decision rather than the server's config. The companion
 * expiry path — an authenticated session that really did end — stays covered by
 * auth-expiry.spec.ts, and must keep passing: this fix narrows that remedy, it
 * does not remove it.
 */

// No stored session: this visitor never signed in.
test.use({ storageState: { cookies: [], origins: [] } });

const GUEST_ME = {
  id: 2,
  name: 'Guest',
  locale: 'en',
  theme: 'dark',
  role: { anonymous: true, viewer: true, download: true },
  features: { anon_browse: true, hide_books: true, mail_configured: false,
    public_registration: false, kobo_sync: false },
  instance_name: 'Calibre-Web NextGen',
  display: { books_per_page: 24, random_books: 4 },
  catalog: { default_filter: null },
  avatar: null,
};

const BOOK = {
  id: 2,
  title: 'The Picture of Dorian Gray',
  authors: [{ id: 1, name: 'Oscar Wilde' }],
  series: null,
  series_index: '',
  rating: null,
  cover_url: null,
  pubdate: null,
  date_added: null,
  last_modified: null,
  description_html: null,
  original_filename: null,
  tags: [],
  languages: [],
  publishers: [],
  identifiers: [],
  formats: [{
    format: 'EPUB',
    size_bytes: 437148,
    download_url: '/download/2/epub/The Picture of Dorian Gray - Oscar Wilde',
    read_url: '/read/2/epub',
  }],
  read: false,
  archived: false,
  favorited: false,
  hidden: false,
  in_progress: false,
  kosync_progress: null,
  kosync_progress_timestamp: null,
  kosync_progress_created_at: null,
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

/** Count logout navigations without letting one actually happen — a real one
 *  would destroy the session server-side and take the page with it. */
async function holdLogoutNavigation(page: Page) {
  let navigations = 0;
  await page.route('**/logout*', async (route) => {
    if (route.request().isNavigationRequest()) navigations += 1;
    await route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Logout captured</title>' });
  });
  return () => navigations;
}

/** Serve the app as it is served to a guest: /me answers for the Guest row, and
 *  the two per-user reader endpoints refuse an anonymous caller. */
async function browseAsGuest(page: Page) {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill(json(GUEST_ME)));
  await page.route('**/api/v1/books/2', (route) => route.fulfill(json(BOOK)));
  await page.route('**/api/v1/books/*/bookmark**', (route) => route.fulfill(
    json({ error: { code: 'unauthorized', message: 'You must be signed in' } }, 401)));
  await page.route('**/api/v1/reader/settings', (route) => route.fulfill(
    json({ error: { code: 'unauthorized', message: 'You must be signed in' } }, 401)));
  // Serve the book itself from a fixture: the assertion is about the reader
  // starting, and a download that depends on the container's library contents
  // would fail for reasons that have nothing to do with #1074.
  await page.route('**/download/**', (route) => route.fulfill({
    status: 200, contentType: 'application/epub+zip', path: EPUB_FIXTURE }));
}

test.describe('guest opening a book (#1074)', () => {
  test('a 401 meant for a signed-in user does not log the guest out', async ({ page }) => {
    const logoutNavigations = await holdLogoutNavigation(page);
    await browseAsGuest(page);

    await page.goto('/app/read/2');
    // Give the reader's own fetches time to fail and be judged. Without the fix
    // the /logout navigation lands well inside this window.
    await page.waitForTimeout(4000);

    expect(logoutNavigations()).toBe(0);
    await expect(page).toHaveURL(/\/app\/read\/2$/);
  });

  test('the reader still boots without server-side settings', async ({ page }) => {
    await holdLogoutNavigation(page);
    await browseAsGuest(page);

    await page.goto('/app/read/2');

    // Hydration used to wait for a reader-settings payload the guest can never
    // receive, so epub.js never started and the reader sat empty forever. The
    // iframe is epub.js having actually rendered the book.
    await expect(page.locator('iframe')).toBeAttached({ timeout: 20_000 });
  });
});
