import { test, expect, Page } from '@playwright/test';

/*
 * #1584 — "New UI: PDF on iPad won't be displayed completely": opening any PDF
 * showed its first page and nothing else. iPadOS only, new UI only.
 *
 * The reader pointed its <iframe> at the raw file (/show/<id>/pdf) and left the
 * rendering to whatever PDF viewer the engine ships. Every browser on iOS and
 * iPadOS is WebKit underneath, and WebKit renders a single page when its native
 * PDF view is embedded in a subframe. That is exactly the matrix the reporter
 * measured: broken on iPadOS Safari AND Firefox, fine on macOS and Android, and
 * fine in the classic UI — which has always rendered PDFs with the bundled
 * pdf.js instead of the native viewer.
 *
 * The fix stops depending on a native viewer at all: the reader now embeds the
 * same pdf.js page the classic UI uses (/read/<id>/pdf), which paints to
 * <canvas> and behaves identically on every engine. The reporter's own testing
 * is the evidence that this works on their hardware — they confirmed the classic
 * UI, i.e. this same viewer, renders correctly on the same iPad.
 *
 * Chromium renders raw-PDF iframes fine, so the iPadOS symptom itself cannot be
 * reproduced in this harness — the same shape as #716 in download.spec.ts. The
 * guard is therefore the contract that actually broke, in two halves: the reader
 * must hand the file to pdf.js, and pdf.js must paint more than one page.
 */

async function pdfBookId(page: Page): Promise<number> {
  const res = await page.request.get('/api/v1/books?per_page=200');
  expect(res.ok(), `/api/v1/books returned ${res.status()}`).toBeTruthy();
  const { items } = await res.json();
  const book = (items ?? []).find((b: { formats?: string[] }) =>
    (b.formats ?? []).some((f) => String(f).toLowerCase() === 'pdf'));
  // Deliberately not test.skip(): a seed with no PDF would turn this guard into
  // a silent no-op, which is how the regression comes back unnoticed.
  expect(book, 'the e2e library seed must contain a book with a PDF format').toBeTruthy();
  return book.id;
}

test('the PDF reader embeds pdf.js, not the engine\'s native viewer (#1584)', async ({ page }) => {
  const id = await pdfBookId(page);
  await page.goto(`/app/view/${id}/pdf`);

  const frame = page.locator('iframe').first();
  await expect(frame).toBeVisible();
  // Matched at the end so the subpath project (/<prefix>/read/<id>/pdf) passes
  // too. A src of /show/<id>/pdf — the raw file — is the pre-fix state.
  await expect(frame).toHaveAttribute('src', new RegExp(`/read/${id}/pdf$`));
});

test('pdf.js paints every page, not just the first (#1584)', async ({ page }) => {
  const id = await pdfBookId(page);
  await page.goto(`/app/view/${id}/pdf`);

  const viewer = page.frameLocator('iframe');
  await expect(viewer.locator('#viewerContainer')).toBeVisible({ timeout: 20_000 });

  // The reported symptom was "only the first page is visible". pdf.js builds a
  // .page node per page as soon as the document loads, so a multi-page fixture
  // has to produce more than one of them.
  await expect
    .poll(() => viewer.locator('#viewer.pdfViewer .page').count(), { timeout: 20_000 })
    .toBeGreaterThan(1);
});
