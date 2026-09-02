import { expect, test, type Page, type Route } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import type { Book, BookDetail, BooksPage, Me, SearchOptions } from '../src/lib/api';

const STABLE_NOW = new Date('2026-01-15T12:00:00.000Z');
const SCREENSHOT_STYLE = fileURLToPath(new URL('./visual-regression.css', import.meta.url));
const DETAIL_ID = 9_001;

const BOOKS: Book[] = [
  ['The Atlas of Quiet Rooms', 'Mara Bell', 'Cartographer Stories', false],
  ['A Field Guide to Moonlit Libraries', 'Jonas Vale', null, true],
  ['The Clockmaker’s Last Map', 'Iris North', 'Meridian Cycle', false],
  ['Small Fires in the Archive', 'Nadia Grey', null, false],
  ['Borrowed Constellations', 'Theo March', 'Meridian Cycle', true],
  ['Notes from the Glass Harbor', 'Elian Shore', null, false],
  ['A Practical History of Impossible Doors', 'Cleo Winter', 'Cartographer Stories', false],
  ['The Paper Observatory', 'Sam Rowan', null, false],
].map(([title, author, series, read], index) => ({
  id: DETAIL_ID + index,
  title: String(title),
  authors: [String(author)],
  series: series === null ? null : String(series),
  series_index: series === null ? null : index + 1,
  cover_url: `/visual-fixture/cover/${DETAIL_ID + index}.svg`,
  formats: ['EPUB', 'PDF'],
  tags: ['Fiction', 'Reference'],
  date_added: '2026-01-10T10:00:00Z',
  last_modified: '2026-01-12T10:00:00Z',
  read: Boolean(read),
  in_progress: index === 2,
  archived: false,
  hidden: false,
  in_my_library: true,
}));

const BOOK_PAGE: BooksPage = { items: BOOKS, page: 1, per_page: 24, total: BOOKS.length };

const BOOK_DETAIL: BookDetail = {
  id: DETAIL_ID,
  title: 'The Atlas of Quiet Rooms',
  authors: [{ id: 501, name: 'Mara Bell' }],
  series: { id: 601, name: 'Cartographer Stories' },
  series_index: '1',
  rating: 9,
  cover_url: `/visual-fixture/cover/${DETAIL_ID}.svg`,
  cover_srcset: null,
  pubdate: '2022-01-01T00:00:00Z',
  date_added: '2026-01-10T10:00:00Z',
  last_modified: '2026-01-12T10:00:00Z',
  description_html: '<p>A deterministic description long enough to exercise the detail-page measure without depending on private library metadata.</p>',
  original_filename: 'atlas-of-quiet-rooms.epub',
  tags: [
    { id: 701, name: 'Architecture' },
    { id: 702, name: 'Maps' },
    { id: 703, name: 'Speculative fiction' },
  ],
  languages: [{ id: 'eng', name: 'English' }],
  publishers: [{ id: 801, name: 'North Window Press' }],
  identifiers: [{ type: 'isbn', val: '9780000000002', url: null, label: 'ISBN' }],
  custom_columns: [],
  formats: [
    { format: 'EPUB', size_bytes: 1_572_864, download_url: '/download/9001/epub', read_url: '/read/9001/epub' },
    { format: 'PDF', size_bytes: 2_621_440, download_url: '/download/9001/pdf', read_url: '/read/9001/pdf' },
  ],
  read: false,
  archived: false,
  favorited: true,
  hidden: false,
  annotation_count: 3,
  in_my_library: true,
  in_progress: true,
  kosync_progress: 42,
  kosync_progress_timestamp: '2026-01-14T08:00:00Z',
  kosync_progress_created_at: '2026-01-11T08:00:00Z',
  convert_options: { sources: ['EPUB', 'PDF'], targets: ['EPUB', 'PDF'] },
};

const SEARCH_OPTIONS: SearchOptions = {
  tags: [{ id: 701, name: 'Architecture' }, { id: 702, name: 'Maps' }],
  series: [{ id: 601, name: 'Cartographer Stories' }],
  languages: [{ id: 'eng', name: 'English' }, { id: 'fra', name: 'Français' }],
  formats: ['EPUB', 'PDF'],
};

function me(locale: 'en' | 'fr'): Me {
  return {
    id: 1,
    name: 'Visual Tester',
    locale,
    theme: 'dark',
    role: {
      admin: true, viewer: true, edit: true, download: true,
      upload: true, delete_books: true,
    },
    sidebar: {
      discover: true, hot: true, rated: true, favorites: true, archived: true,
      authors: true, series: true, tags: true, publishers: true, languages: true,
      ratings: true, formats: true, shelves: true, table: true,
    },
    preferences: { discover_visible: false, show_hidden_books: false, card_actions_visible: true },
    avatar: null,
    features: {
      hide_books: true,
      mail_configured: false,
      public_registration: false,
      anon_browse: false,
      kobo_sync: false,
      kobo_two_way_annotations: false,
      uploading: true,
    },
    instance_name: 'Visual Fixture Library',
    display: { books_per_page: 24, random_books: 4 },
    catalog: { default_filter: null },
    library_mode: 'monolibrary',
    my_library_seeded: true,
    show_my_library_intro: false,
    can_switch_library_mode: true,
    library_mode_managed: false,
  };
}

function fulfillJson(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(value),
  });
}

async function installStableFixtures(page: Page, locale: 'en' | 'fr' = 'en') {
  await page.clock.install({ time: STABLE_NOW });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.route('**/visual-fixture/cover/*.svg', (route) => route.fulfill({
    status: 200,
    contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 360"><rect width="240" height="360" fill="#202837"/><rect x="22" y="22" width="196" height="316" rx="8" fill="#344258"/><path d="M56 250 120 96l64 154" fill="none" stroke="#9cb6d8" stroke-width="12"/><circle cx="120" cy="250" r="20" fill="#d7e2f0"/></svg>',
  }));
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/api/v1/auth/me')) return fulfillJson(route, me(locale));
    if (path.endsWith(`/api/v1/books/${DETAIL_ID}`)) return fulfillJson(route, BOOK_DETAIL);
    if (path.endsWith(`/api/v1/books/${DETAIL_ID}/shelves`)) return fulfillJson(route, { shelf_ids: [] });
    if (path.endsWith('/api/v1/books')) return fulfillJson(route, BOOK_PAGE);
    if (path.endsWith('/api/v1/search/options')) return fulfillJson(route, SEARCH_OPTIONS);
    if (path.endsWith('/api/v1/shelves')) return fulfillJson(route, { items: [] });
    if (path.includes('/api/v1/notices')) return fulfillJson(route, { notices: [], summary: { count: 0 } });
    if (path.includes('/api/v1/delivery-devices')) return fulfillJson(route, { devices: [] });
    return route.continue();
  });
}

async function settle(page: Page) {
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForFunction(() => [...document.images].every((image) => image.complete));
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('cwng:catalog-density-v1', 'comfortable');
    localStorage.setItem('cwng:card-actions-hidden-v1', '0');
  });
});

test('desktop sign-in protects the unauthenticated entry layout', async ({ page }) => {
  await page.clock.install({ time: STABLE_NOW });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.route('**/api/v1/auth/me', (route) => fulfillJson(route, {
    error: { code: 'unauthenticated', message: 'Login required' },
  }, 401));
  await page.route('**/api/v1/auth/config', (route) => fulfillJson(route, {
    instance_name: 'Visual Fixture Library',
    public_registration: false,
    register_email: false,
    mail_configured: false,
    standard_login_disabled: false,
    remote_login: true,
    oauth_providers: [{ id: 1, name: 'Continue with Household SSO', url: '/oauth/fixture' }],
  }));
  await page.goto('/app/login');
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('login-desktop.png', { stylePath: SCREENSHOT_STYLE });
});

test('desktop catalog protects the primary library canvas', async ({ page }) => {
  await installStableFixtures(page);
  await page.goto('/app');
  await expect(page.getByTestId('catalog-grid')).toBeVisible();
  await expect(page.locator('a[aria-label^="Open details for"]').first()).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('catalog-desktop.png', { stylePath: SCREENSHOT_STYLE });
});

test('mobile open drawer protects navigation overlay and reflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStableFixtures(page);
  await page.goto('/app');
  await expect(page.getByTestId('catalog-grid')).toBeVisible();
  await page.getByRole('button', { name: /open navigation/i }).click();
  await expect(page.getByRole('navigation')).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('catalog-mobile-drawer.png', { stylePath: SCREENSHOT_STYLE });
});

test('desktop detail protects the high-density book action layout', async ({ page }) => {
  await installStableFixtures(page);
  await page.goto(`/app/book/${DETAIL_ID}`);
  await expect(page.getByRole('heading', { name: BOOK_DETAIL.title })).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('book-detail-desktop.png', { stylePath: SCREENSHOT_STYLE });
});

test('mobile detail protects the narrow action and metadata stack', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installStableFixtures(page);
  await page.goto(`/app/book/${DETAIL_ID}`);
  await expect(page.getByRole('heading', { name: BOOK_DETAIL.title })).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('book-detail-mobile.png', { stylePath: SCREENSHOT_STYLE });
});

test('French advanced search protects locale-driven control wrapping', async ({ page }) => {
  await installStableFixtures(page, 'fr');
  await page.goto('/app/search');
  await expect(page.locator('html')).toHaveAttribute('lang', 'fr');
  await expect(page.getByTestId('advanced-search-form')).toBeVisible();
  await settle(page);
  await expect(page).toHaveScreenshot('advanced-search-fr-desktop.png', { stylePath: SCREENSHOT_STYLE });
});
