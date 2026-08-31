import { expect, test, type Browser, type BrowserContext, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

const PASSWORD = 'CWNG-role-matrix-42!';
const EPUB_FORMATS = new Set(['epub', 'kepub']);
const COMIC_FORMATS = new Set(['cbz', 'cbr', 'cbt']);

type RoleCase = {
  label: string;
  viewer: boolean;
  download: boolean;
};

type BookDetail = {
  id: number;
  title: string;
  formats: Array<{
    format: string;
    download_url: string;
    read_url: string;
    content_url?: string;
  }>;
};

type BookListItem = {
  id: number;
  title: string;
  formats: string[];
};

const ROLE_CASES: RoleCase[] = [
  { label: 'viewer-only', viewer: true, download: false },
  { label: 'download-only', viewer: false, download: true },
  { label: 'viewer-and-download', viewer: true, download: true },
];

async function csrf(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json() as { csrf_token: string }).csrf_token;
}

async function login(page: Page, username: string): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    headers: { 'X-CSRFToken': await csrf(page) },
    data: { username, password: PASSWORD },
  });
  expect(response.ok(), `${username} could not log in: ${await response.text()}`).toBe(true);
}

async function contextFor(
  browser: Browser,
  baseURL: string | undefined,
  username: string,
): Promise<{ context: BrowserContext; page: Page }> {
  // Deliberately empty: every matrix row proves its own real server session and
  // cannot inherit the admin storageState created by global.setup.ts.
  const context = await browser.newContext({
    baseURL,
    storageState: { cookies: [], origins: [] },
  });
  const page = await context.newPage();
  await login(page, username);
  return { context, page };
}

async function discoverFixtureBooks(page: Page): Promise<{
  readableBookId: number;
  comicBookId: number;
}> {
  const response = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  expect(response.ok(), `could not query the e2e book seed: ${await response.text()}`).toBe(true);
  const payload = await response.json() as { items?: BookListItem[]; books?: BookListItem[] };
  const books = payload.items || payload.books || [];
  const readable = books.find((book) =>
    (book.formats || []).some((format) => EPUB_FORMATS.has(String(format).toLowerCase())));
  const comic = books.find((book) =>
    (book.formats || []).some((format) => COMIC_FORMATS.has(String(format).toLowerCase())));

  expect(
    readable,
    'the e2e library seed must contain a book with an EPUB or KEPUB format',
  ).toBeTruthy();
  expect(
    comic,
    'the e2e library seed must contain a book with a CBZ, CBR, or CBT format',
  ).toBeTruthy();
  return { readableBookId: readable!.id, comicBookId: comic!.id };
}

test('viewer and download roles independently govern every book affordance (F-ed109c)', async ({
  page: adminPage,
  browser,
  baseURL,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the authorization matrix runs once on desktop');

  const adminCsrf = await csrf(adminPage);
  const { readableBookId, comicBookId } = await discoverFixtureBooks(adminPage);
  const suffix = `${testInfo.workerIndex}-${Date.now()}`;
  const users: Array<RoleCase & { id: number; name: string }> = [];

  try {
    for (const roleCase of ROLE_CASES) {
      const name = `role-${roleCase.label}-${suffix}`;
      const created = await adminPage.request.post('/api/v1/admin/users', {
        headers: { 'X-CSRFToken': adminCsrf },
        data: {
          name,
          email: `${name}@example.test`,
          password: PASSWORD,
          // Edit access makes the edit-book file list reachable so the same
          // download-role contract is exercised on every SPA book surface.
          roles: { viewer: roleCase.viewer, download: roleCase.download, edit: true },
        },
      });
      expect(created.ok(), `${roleCase.label} user creation failed: ${await created.text()}`).toBe(true);
      users.push({ ...roleCase, ...(await created.json() as { id: number; name: string }) });
    }

    for (const user of users) {
      const { context, page } = await contextFor(browser, baseURL, user.name);
      try {
        // The API's own role answer is the oracle for what the SPA may render.
        const meResponse = await page.request.get('/api/v1/auth/me');
        expect(meResponse.ok(), await meResponse.text()).toBe(true);
        const me = await meResponse.json() as { role: { viewer: boolean; download: boolean } };
        expect(me.role.viewer, `${user.label}: API viewer role`).toBe(user.viewer);
        expect(me.role.download, `${user.label}: API download role`).toBe(user.download);

        const detailResponse = await page.request.get(`/api/v1/books/${readableBookId}`);
        expect(detailResponse.ok(), `book ${readableBookId} disappeared from the role-matrix seed`).toBe(true);
        const book = await detailResponse.json() as BookDetail;
        const readable = book.formats.find((format) => EPUB_FORMATS.has(format.format.toLowerCase()));
        expect(readable, `book ${readableBookId} needs an EPUB-family format`).toBeTruthy();
        const contentUrl = readable!.content_url
          || `/show/${readableBookId}/${readable!.format.toLowerCase()}`;

        // Pin the unchanged server contract before checking its presentation.
        const inline = await page.request.get(contentUrl);
        expect(inline.status(), `${user.label}: viewer-gated inline route`).toBe(user.viewer ? 200 : 403);
        const classicReader = await page.request.get(`/read/${readableBookId}/${readable!.format.toLowerCase()}`);
        expect(classicReader.status(), `${user.label}: viewer-gated classic reader route`).toBe(user.viewer ? 200 : 403);
        const download = await page.request.get(readable!.download_url);
        expect(download.status(), `${user.label}: download-gated file route`).toBe(user.download ? 200 : 403);

        // The SPA comic API serves real page bytes, so it must follow viewer
        // exactly like Classic's comic reader. In particular, download alone
        // must not grant access, while viewer alone must continue to work.
        const comicDetailResponse = await page.request.get(`/api/v1/books/${comicBookId}`);
        expect(comicDetailResponse.ok(), `book ${comicBookId} disappeared from the comic seed`).toBe(true);
        const comicBook = await comicDetailResponse.json() as BookDetail;
        const comicFormat = comicBook.formats.find((format) => COMIC_FORMATS.has(format.format.toLowerCase()));
        expect(comicFormat, `book ${comicBookId} needs a comic format`).toBeTruthy();
        const classicComic = await page.request.get(`/read/${comicBookId}/${comicFormat!.format.toLowerCase()}`);
        expect(classicComic.status(), `${user.label}: viewer-gated classic comic reader`).toBe(user.viewer ? 200 : 403);
        const comicInfo = await page.request.get(`/api/v1/books/${comicBookId}/comic`);
        expect(comicInfo.status(), `${user.label}: viewer-gated SPA comic metadata`).toBe(user.viewer ? 200 : 403);
        const comicPage = await page.request.get(`/api/v1/books/${comicBookId}/comic/0`);
        expect(comicPage.status(), `${user.label}: viewer-gated SPA comic page bytes`).toBe(user.viewer ? 200 : 403);
        if (user.viewer) {
          expect((await comicPage.body()).byteLength, `${user.label}: comic page contains image bytes`).toBeGreaterThan(0);
          expect(comicPage.headers()['content-type'], `${user.label}: comic page content type`).toMatch(/^image\//);
        }

        // New UI detail: each affordance follows its own API role bit.
        await page.goto(`/app/book/${readableBookId}`);
        await expect(page.getByRole('heading', { name: book.title })).toBeVisible();
        await expect(
          page.locator(`a[href$="/read/${readableBookId}"], a[href*="/view/${readableBookId}/"]`),
          `${user.label}: New UI detail Read affordance must match API role.viewer`,
        ).toHaveCount(me.role.viewer ? 1 : 0);
        await expect(
          page.locator(`a[href*="/download/${readableBookId}/"]`),
          `${user.label}: New UI detail download affordances must match API role.download`,
        ).toHaveCount(me.role.download ? book.formats.length : 0);

        await page.goto(`/app/book/${readableBookId}/edit`);
        await expect(page.getByRole('heading', { name: 'Files' })).toBeVisible();
        await expect(
          page.locator(`a[href*="/download/${readableBookId}/"]`),
          `${user.label}: New UI edit-page download affordances must match API role.download`,
        ).toHaveCount(me.role.download ? book.formats.length : 0);

        // New UI catalog cards use the same viewer answer as detail.
        await page.goto(`/app/?q=${encodeURIComponent(book.title)}`);
        const card = page.locator(`a[href$="/book/${readableBookId}"]`).first();
        await expect(card).toBeVisible();
        const cardWrap = card.locator('..');
        await expect(
          cardWrap.locator(`a[href$="/read/${readableBookId}"], a[href*="/view/${readableBookId}/"]`),
          `${user.label}: New UI card Read affordance must match API role.viewer`,
        ).toHaveCount(me.role.viewer ? 1 : 0);

        if (me.role.viewer) {
          expect(contentUrl, `${user.label}: reader must use the viewer-gated EPUB content URL`)
            .toBe(`/show/${readableBookId}/${readable!.format.toLowerCase()}`);
          const contentResponse = page.waitForResponse((response) =>
            new URL(response.url()).pathname === contentUrl,
          );
          await page.goto(`/app/read/${readableBookId}`);
          // An attached epub.js iframe proves the viewer fetched and parsed the
          // actual book bytes; the pre-fix 403 path never creates one.
          await expect(page.locator('iframe').first()).toBeAttached({ timeout: 20_000 });
          expect((await contentResponse).status(), `${user.label}: SPA reader content response`).toBe(200);
          await expect(page.getByText(/Could not load the book file \(403\)/)).toHaveCount(0);
        }

        // Classic is the immutable reference: same session, user and book.
        await page.context().addCookies([{
          name: 'cwng_prefer_spa', value: '0', url: new URL(page.url()).origin,
        }]);
        await page.goto(`/book/${readableBookId}`, { waitUntil: 'domcontentloaded' });
        await expect(page.locator('a.book-read-cta')).toHaveCount(me.role.viewer ? 1 : 0);
        const classicDownloads = page.locator(`a[href*="/download/${readableBookId}/"]`);
        if (me.role.download) await expect(classicDownloads.first()).toBeAttached();
        else {
          const hrefs = await classicDownloads.evaluateAll((links) =>
            links.map((link) => link.getAttribute('href')));
          expect(hrefs, `${user.label}: Classic must not expose download links`).toEqual([]);
        }
      } finally {
        await context.close();
      }
    }
  } finally {
    for (const user of users) {
      const response = await adminPage.request.post(`/api/v1/admin/users/${user.id}/delete`, {
        headers: { 'X-CSRFToken': adminCsrf },
      });
      expect(response.status(), `cleanup failed for ${user.name}: ${await response.text()}`).toBe(204);
    }
  }
});
