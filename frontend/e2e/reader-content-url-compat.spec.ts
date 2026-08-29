import { expect, test, type Page } from '@playwright/test';

type BookListItem = { id: number; formats?: string[] };
type BookDetail = {
  formats?: Array<{
    format: string;
    content_url?: string;
    download_url: string;
  }>;
};

async function findEpub(page: Page): Promise<{
  id: number;
  format: string;
  contentUrl: string;
}> {
  const listResponse = await page.request.get('/api/v1/books?page=1&per_page=200&sort=new');
  expect(listResponse.ok(), await listResponse.text()).toBe(true);
  const list = await listResponse.json() as { items?: BookListItem[]; books?: BookListItem[] };
  const summary = (list.items || list.books || []).find((book) =>
    (book.formats || []).some((format) => ['epub', 'kepub'].includes(format.toLowerCase())));
  expect(summary, 'the e2e seed must contain an EPUB-family book').toBeTruthy();

  const detailResponse = await page.request.get(`/api/v1/books/${summary!.id}`);
  expect(detailResponse.ok(), await detailResponse.text()).toBe(true);
  const detail = await detailResponse.json() as BookDetail;
  const format = detail.formats?.find((candidate) =>
    ['epub', 'kepub'].includes(candidate.format.toLowerCase()));
  expect(format, `book ${summary!.id} must expose an EPUB-family format`).toBeTruthy();
  expect(format!.content_url, 'the current API must expose content_url before the compatibility shim strips it')
    .toBe(`/show/${summary!.id}/${format!.format.toLowerCase()}`);
  return { id: summary!.id, format: format!.format, contentUrl: format!.content_url! };
}

test('reader derives the viewer-gated content route when an older API omits content_url', async ({ page }) => {
  test.setTimeout(45_000);
  const epub = await findEpub(page);
  let strippedCurrentPayload = false;
  const downloadRequests: string[] = [];

  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith(`/download/${epub.id}/`)) downloadRequests.push(path);
  });
  await page.route(`**/api/v1/books/${epub.id}`, async (route) => {
    const response = await route.fetch();
    const detail = await response.json() as BookDetail;
    detail.formats = (detail.formats || []).map(({ content_url: ignored, ...format }) => format);
    strippedCurrentPayload = true;
    await route.fulfill({ response, json: detail });
  });

  const contentResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === epub.contentUrl);
  await page.goto(`/app/read/${epub.id}`);
  await expect(page.locator('iframe').first()).toBeAttached({ timeout: 20_000 });

  expect(strippedCurrentPayload, 'the test must exercise an API payload without content_url').toBe(true);
  expect((await contentResponse).status()).toBe(200);
  expect(downloadRequests, 'compatibility must never fall back to the download-gated route').toEqual([]);
});
