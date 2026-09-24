import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const quote = 'a native passage spans nodes.';
const builder = fileURLToPath(new URL('../../tests/fixtures/reader_archive.py', import.meta.url));
const archive = (chapters: string[]) => execFileSync('python3', [builder], { input: JSON.stringify(chapters) });
const webCfi = 'epubcfi(/6/2!/4/2[existing],/1:0,/1:20)';
const foreignCfi = 'epubcfi(/6/4!/4/2[wrapper]/2[kobo.15.1],/1:15,/1:63)';
const initialBookmark = 'epubcfi(/6/2!/4/2[existing]/1:0)';

async function openFixture(page: Page, duplicate = false, nativeSpans = false) {
  const epub = archive([
    '<p id="existing">Existing web passage remains.</p>' + (nativeSpans ? `<p><span id="kobo.15.1">${quote}</span></p>` : duplicate ? `<p>${quote}</p>` : ''),
    nativeSpans ? `<p><span id="kobo.15.1">${quote}</span></p>` : `<p>😀 Before: a native <em>passage spans</em> nodes. After.</p>`,
  ]);
  const list = await (await page.request.get('/api/v1/books?per_page=1')).json();
  const id = list.items[0].id;
  const bytesRequested: string[] = [];
  const bookmarkWrites: unknown[] = [];
  const edits: unknown[] = [];
  let overlappingWebCfi: string | null = null;
  await page.route(`**/api/v1/books/${id}`, async (route) => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.formats = ['EPUB', 'KEPUB'].map((format) => ({
      format, size_bytes: epub.length, content_url: `/show/${id}/${format.toLowerCase()}`,
      download_url: `/download/${id}/${format.toLowerCase()}`, read_url: `/read/${id}/${format.toLowerCase()}`,
    }));
    await route.fulfill({ response, json: detail });
  });
  await page.route(`**/show/${id}/*`, async (route) => {
    bytesRequested.push(new URL(route.request().url()).pathname);
    await route.fulfill({ contentType: 'application/epub+zip', body: epub });
  });
  await page.route(`**/api/v1/books/${id}/bookmark*`, async (route) => {
    if (route.request().method() !== 'GET') bookmarkWrites.push(route.request().postDataJSON());
    await route.fulfill({ json: { bookmark: initialBookmark } });
  });
  await page.route(`**/annotations/${id}/data.json`, (route) => route.fulfill({ json: {
    annotations: [
      { annotation_id: 'web', cfi_range: overlappingWebCfi || webCfi, position_type: 'cfi',
        highlighted_text: overlappingWebCfi ? quote : 'Existing web passage', highlight_color: 'yellow', source: 'webreader' },
      { annotation_id: 'native', cfi_range: foreignCfi, start_kobospan: 'kobo.15.1',
        content_id: nativeSpans ? 'fixture!!OPS/part1/chapter.xhtml' : null,
        start_offset: 0, end_offset: quote.length,
        end_kobospan: 'kobo.15.1', highlighted_text: quote, highlight_color: 'yellow', source: 'kobo' },
    ], devices: {},
  } }));
  await page.route(`**/annotations/${id}/native`, async (route) => {
    edits.push(route.request().method() === 'DELETE' ? 'DELETE' : route.request().postDataJSON());
    await route.fulfill({ json: { annotation_id: 'native' } });
  });
  await page.goto(`/app/read/${id}`);
  await expect(page.locator('[data-id="web"] rect').first()).toBeAttached({ timeout: 20_000 });
  return { id, bytesRequested, bookmarkWrites, edits,
    overlapWith: (cfi: string) => { overlappingWebCfi = cfi; } };
}

test('native highlight maps into the existing EPUB, preserving web highlights and preview bookmarks', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const fixture = await openFixture(page);
  await page.getByRole('button', { name: 'Highlights and notes', exact: true }).click();
  const jump = page.getByRole('button', { name: `${quote} kobo`, exact: true });
  await expect(jump).toBeEnabled();
  await jump.click();
  const mark = page.locator('[data-id="native"]');
  await expect(mark.locator('rect').first()).toBeAttached();
  expect(await mark.getAttribute('data-epubcfi')).not.toBe(foreignCfi);
  expect(fixture.bytesRequested).toEqual([`/show/${fixture.id}/epub`]);
  // An actual click on the mapped overlay must update the original annotation,
  // never send the display-only EPUB CFI back as its Kobo source position.
  await mark.dispatchEvent('click');
  await page.getByRole('button', { name: 'Green', exact: true }).click();
  await expect.poll(() => fixture.edits.length).toBe(1);
  expect(fixture.edits).toEqual([{ highlight_color: 'green' }]);
  // Observe beyond the 800ms save debounce. A pending initial-position save
  // may finish during preview; only saving the preview chapter is a regression.
  await page.waitForTimeout(1000);
  for (const write of fixture.bookmarkWrites) {
    expect((write as { bookmark: string }).bookmark.split('!')[0])
      .toBe(initialBookmark.split('!')[0]);
  }
  expect(errors).toEqual([]);
});

test('ambiguous native quote remains readable without using its foreign CFI', async ({ page }) => {
  await openFixture(page, true);
  await page.getByRole('button', { name: 'Highlights and notes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Existing web passage', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: `${quote} kobo`, exact: true })).toBeDisabled();
  await expect(page.locator('[data-id="native"]')).toHaveCount(0);
});

test('a valid native chapter and span preserve an existing highlight even when its quote repeats', async ({ page }) => {
  await openFixture(page, false, true);
  await page.getByRole('button', { name: 'Highlights and notes', exact: true }).click();
  const jump = page.getByRole('button', { name: `${quote} kobo`, exact: true });
  await expect(jump).toBeEnabled();
  await jump.click();
  const mark = page.locator('[data-id="native"]');
  await expect(mark.locator('rect').first()).toBeAttached();
  expect(await mark.getAttribute('data-epubcfi')).toMatch(/^epubcfi\(\/6\/4(?:\[[^\]]*\])?!/);
});

test('overlapping native and web highlights remain individually editable and a deletion repaints the survivor', async ({ page }) => {
  const fixture = await openFixture(page);
  const jumpTo = async (name: string) => {
    await page.getByRole('button', { name: 'Highlights and notes', exact: true }).click();
    const jump = page.getByRole('button', { name, exact: true });
    await expect(jump).toBeEnabled();
    await jump.click();
  };
  await jumpTo(`${quote} kobo`);
  const native = page.locator('[data-id="native"]');
  await expect(native.locator('rect').first()).toBeAttached();
  const cfi = await native.getAttribute('data-epubcfi');
  expect(cfi).toBeTruthy();
  fixture.overlapWith(cfi!);
  await page.reload();
  await jumpTo(quote);
  await expect(page.locator('[data-id="web"] rect').first()).toBeAttached();
  await jumpTo(`${quote} kobo`);
  await expect(native.locator('rect').first()).toBeAttached();
  await native.dispatchEvent('click');
  await page.getByRole('button', { name: 'Remove highlight', exact: true }).click();
  await expect.poll(() => fixture.edits).toEqual(['DELETE']);
  await expect(page.locator('[data-id="web"] rect').first()).toBeAttached();
  await expect(native).toHaveCount(0);
});

test('native mapping proves exact uniqueness with real DOM ranges and refuses incomplete scans', async ({ page }) => {
  // Exercise the actual resolver with browser DOM APIs, including UTF-16 and
  // cross-element ranges. A small archive adapter replaces only archive I/O.
  const source = readFileSync(new URL('../src/lib/reader/nativeAnnotations.ts', import.meta.url), 'utf8');
  const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022 } }).outputText;
  await page.goto('about:blank');
  const results = await page.evaluate(async ({ javascript }) => {
    const { resolveNativeAnnotations } = await import(`data:text/javascript;base64,${btoa(javascript)}`);
    async function resolve(bodies: (string | null)[], quote: string, abortAfterLoad = false,
      extraQuote?: string, native: Record<string, unknown> = {}, urls?: string[]) {
      let cancelled = false;
      const sectionUrls = urls || bodies.map((_, index) => `/OPS/part${index}/chapter.xhtml`);
      const book = {
        spine: { spineItems: bodies.map((_, index) => ({ url: sectionUrls[index],
          cfiFromRange: (range: Range) => JSON.stringify({ text: range.toString(),
            start: range.startOffset, end: range.endOffset, section: index }),
        })) },
        load: async (url: string) => {
          const index = sectionUrls.indexOf(url);
          if (bodies[index] === null) throw new Error('Unreadable chapter');
          if (abortAfterLoad) cancelled = true;
          return new DOMParser().parseFromString(`<html><head><title>${quote}</title></head><body>${bodies[index]}</body></html>`, 'application/xhtml+xml');
        },
      };
      const rows = [{ annotation_id: 'native', highlighted_text: quote, start_kobospan: 'start', end_kobospan: 'end', ...native }];
      if (extraQuote) rows.push({ ...rows[0], annotation_id: 'bad', highlighted_text: extraQuote });
      return Array.from(await resolveNativeAnnotations(book, rows, () => cancelled));
    }
    return {
      exact: await resolve(['<p>😀 Before: a native <em>passage spans</em> nodes. After.</p>'], 'a native passage spans nodes.'),
      duplicate: await resolve(['<p>same quote</p>', '<p>same quote</p>'], 'same quote'),
      overlap: await resolve(['<p>aaa</p>'], 'aa'),
      caseMismatch: await resolve(['<p>Same Quote</p>'], 'same quote'),
      unreadable: await resolve(['<p>same quote</p>', null], 'same quote'),
      aborted: await resolve(['<p>same quote</p>'], 'same quote', true),
      nonreading: await resolve(['<script>same quote</script><style>same quote</style><p>same quote</p>'], 'same quote'),
      mixed: await resolve(['<p>unique quote</p><p>left <style>hidden</style>right</p>'], 'unique quote', false, 'left right'),
      nativeWithUnreadableSibling: await resolve(['<p>same quote</p>', '<p><span id="s">same quote</span></p>', null], 'same quote', false, undefined,
        { content_id: 'fixture!!OPS/part1/chapter.xhtml', start_kobospan: 's', end_kobospan: 's', start_offset: 0, end_offset: 10 }),
      ambiguousNativeChapter: await resolve(['<p><span id="s">same quote</span></p>', '<p><span id="s">same quote</span></p>'], 'same quote', false, undefined,
        { content_id: 'fixture!!chapter.xhtml', start_kobospan: 's', end_kobospan: 's', start_offset: 0, end_offset: 10 }),
      duplicateSpanIds: await resolve(['<p><span id="s">same quote</span><span id="s">same quote</span></p>'], 'same quote', false, undefined,
        { content_id: 'fixture!!OPS/part0/chapter.xhtml', start_kobospan: 's', end_kobospan: 's', start_offset: 0, end_offset: 10 }),
      nativeUnicode: await resolve(['<p>a native passage spans nodes.</p>', '<p><span id="s">😀 Before: a native </span><em><span id="e">passage spans nodes. After.</span></em></p>'], 'a native passage spans nodes.', false, undefined,
        { content_id: 'fixture!!OPS/part%31/chapter.xhtml', start_kobospan: 's', end_kobospan: 'e', start_offset: 11, end_offset: 20 }),
      phantomEncodedMember: await resolve(['<p><span id="s">same quote</span></p>', '<p><span id="s">same quote</span></p>'], 'same quote', false, undefined,
        { content_id: 'fixture!!OPS/first%2520chapter.xhtml', start_kobospan: 's', end_kobospan: 's', start_offset: 0, end_offset: 10 },
        ['/OPS/first%20chapter.xhtml', '/OPS/other.xhtml']),
      chapterWithBang: await resolve(['<p><span id="s">same quote</span></p>', '<p><span id="s">same quote</span></p>'], 'same quote', false, undefined,
        { content_id: 'fixture!!OPS/part!!one/chapter.xhtml', start_kobospan: 's', end_kobospan: 's', start_offset: 0, end_offset: 10 },
        ['/OPS/part!!one/chapter.xhtml', '/OPS/other.xhtml']),
    };
  }, { javascript });
  expect(results.exact).toEqual([['native', JSON.stringify({ text: quote, start: 11, end: 7, section: 0 })]]);
  for (const key of ['duplicate', 'overlap', 'caseMismatch', 'unreadable', 'aborted', 'ambiguousNativeChapter', 'duplicateSpanIds', 'phantomEncodedMember'] as const) {
    expect(results[key], key).toEqual([]);
  }
  expect(results.nonreading).toHaveLength(1);
  expect(results.mixed).toEqual([['native', JSON.stringify({ text: 'unique quote', start: 0, end: 12, section: 0 })]]);
  expect(results.nativeWithUnreadableSibling).toEqual([['native', JSON.stringify({ text: 'same quote', start: 0, end: 10, section: 1 })]]);
  expect(results.nativeUnicode).toEqual([['native', JSON.stringify({ text: quote, start: 11, end: 20, section: 1 })]]);
  expect(results.chapterWithBang).toEqual([['native', JSON.stringify({ text: 'same quote', start: 0, end: 10, section: 0 })]]);
});
