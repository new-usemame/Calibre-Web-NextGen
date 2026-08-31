import { test, expect, type Page } from '@playwright/test';
import type { BookDetail, BookMetadata } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Fork issue #919 — "[New UI] Bring back rich HTML editor and preview",
 * reported by @mrdynamo, +1'd by @jsparrowio and @Gauva1n, and independently
 * reported through the in-app switch-back feedback form as #1038 ("description
 * editor is severely lacking compared to the classic interface").
 *
 * The classic edit page runs TinyMCE on the description field
 * (cps/static/js/edit_books.js). The New UI shipped a bare <textarea>, so the
 * description showed raw HTML tags with no formatting controls and no preview.
 * @Gauva1n's case is the sharp one: pasting a blurb from Goodreads or Amazon
 * lost its paragraphs and lists entirely.
 *
 * The subtle assertion here is `bold emits a tag, not a style`. execCommand
 * produces <span style="font-weight:bold"> unless styleWithCSS is forced off,
 * and the server strips style attributes — so a CSS-styled bold looks correct
 * while editing and is plain text on the book page. That failure is invisible
 * to any test that only looks at the editor.
 *
 * Mocked metadata so the flow does not depend on what the seeded library holds.
 */

const TARGET_ID = 9401;

function metadata(comments: string): BookMetadata {
  return {
    id: TARGET_ID,
    title: 'A book with a description',
    authors: 'Mock Author',
    series: '',
    series_index: '',
    tags: '',
    publishers: '',
    languages: '',
    comments,
    rating: 0,
    pubdate: '',
    identifiers: [],
    custom_columns: [],
  };
}

function detail(): BookDetail {
  return {
    id: TARGET_ID,
    title: 'A book with a description',
    authors: [{ id: 1, name: 'Mock Author' }],
    series: null, series_index: '', rating: null, cover_url: null,
    pubdate: null, date_added: null, last_modified: null,
    description_html: null, original_filename: null,
    tags: [], languages: [], publishers: [], identifiers: [], formats: [],
    read: false, archived: false, favorited: false, hidden: false,
  } as unknown as BookDetail;
}

/** Serve the edit form, and capture what a save would POST. The book detail is
 *  stubbed too: without it the page 404s for a book id that is not in the
 *  seeded library, which trips the clean-console gate for a fixture reason. */
async function mockBook(page: Page, initialComments: string) {
  const saved: { comments?: string } = {};
  await page.route(`**/api/v1/books/${TARGET_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(detail()),
    });
  });
  await page.route(`**/api/v1/books/${TARGET_ID}/metadata`, async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { comments?: string };
      saved.comments = body?.comments;
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify(metadata(body?.comments ?? '')),
      });
      return;
    }
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(metadata(initialComments)),
    });
  });
  return saved;
}

const editor = (page: Page) => page.getByRole('textbox', { name: 'Description' });

test.describe('New UI description editor (#919)', () => {
  test('offers formatting controls instead of a bare textarea', async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBook(page, '<p>Existing description.</p>');
    await page.goto(`/app/book/${TARGET_ID}/edit`);

    const toolbar = page.getByRole('toolbar', { name: 'Description formatting' });
    await expect(toolbar).toBeVisible();
    for (const label of ['Bold', 'Italic', 'Bulleted list', 'Numbered list', 'Add link']) {
      await expect(toolbar.getByRole('button', { name: label })).toBeVisible();
    }

    // The stored HTML is rendered, not shown as literal tags — the "<p>" the
    // reporter saw in the box.
    await expect(editor(page)).toContainText('Existing description.');
    await expect(editor(page)).not.toContainText('<p>');

    assertNoPageErrors(errors);
  });

  test('has no underline or strikethrough, because the server escapes them', async ({ page }) => {
    await mockBook(page, '');
    await page.goto(`/app/book/${TARGET_ID}/edit`);
    const toolbar = page.getByRole('toolbar', { name: 'Description formatting' });
    await expect(toolbar.getByRole('button', { name: 'Underline' })).toHaveCount(0);
    await expect(toolbar.getByRole('button', { name: 'Strikethrough' })).toHaveCount(0);
  });

  test('bold saves a tag, not an inline style the server would strip', async ({ page }) => {
    const saved = await mockBook(page, '');
    await page.goto(`/app/book/${TARGET_ID}/edit`);

    const box = editor(page);
    await box.click();
    await page.keyboard.type('Bold me');
    await page.keyboard.press('ControlOrMeta+a');
    await page.getByRole('button', { name: 'Bold', exact: true }).click();

    await page.getByRole('button', { name: /^Save/ }).first().click();
    await expect.poll(() => saved.comments).toBeTruthy();

    expect(saved.comments).toContain('Bold me');
    expect(saved.comments).toMatch(/<(strong|b)>/);
    // The regression this test exists for: style attributes do not survive
    // cps/clean_html.py, so a styled bold would silently flatten on display.
    expect(saved.comments).not.toContain('style=');
    expect(saved.comments).not.toContain('font-weight');
  });

  test('a web paste keeps its paragraphs and list, and drops the wrapper markup', async ({ page }) => {
    const saved = await mockBook(page, '');
    await page.goto(`/app/book/${TARGET_ID}/edit`);

    const box = editor(page);
    await box.click();

    // Shape of a real Goodreads/Amazon copy: styled wrappers, a font tag, a
    // class-laden div, a script, and the content that actually matters.
    const pastedHtml = `
      <div class="bookDescription" style="color:#382110">
        <font face="Arial"><b>A gripping tale.</b></font>
        <p>Second paragraph with <i>emphasis</i>.</p>
        <ul><li>First point</li><li>Second point</li></ul>
        <script>window.__pwned = 1;</script>
        <a href="javascript:alert(1)">bad link</a>
        <a href="https://example.com/book">good link</a>
      </div>`;

    await box.evaluate((el, html) => {
      const data = new DataTransfer();
      data.setData('text/html', html);
      data.setData('text/plain', 'A gripping tale.');
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    }, pastedHtml);

    // Content survives, structure survives.
    await expect(box).toContainText('A gripping tale.');
    await expect(box).toContainText('Second paragraph with emphasis.');
    await expect(box.locator('li')).toHaveCount(2);
    await expect(box.locator('strong')).toHaveCount(1);
    await expect(box.locator('em')).toHaveCount(1);

    // Junk does not.
    await expect(box.locator('font')).toHaveCount(0);
    await expect(box.locator('script')).toHaveCount(0);
    await expect(box.locator('[style]')).toHaveCount(0);
    await expect(box.locator('[class]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();

    // Links: the safe one is kept, the javascript: one is reduced to its text.
    await expect(box.locator('a[href="https://example.com/book"]')).toHaveCount(1);
    await expect(box.locator('a[href^="javascript:"]')).toHaveCount(0);
    await expect(box).toContainText('bad link');

    await page.getByRole('button', { name: /^Save/ }).first().click();
    await expect.poll(() => saved.comments).toBeTruthy();
    expect(saved.comments).not.toContain('<script');
    expect(saved.comments).not.toContain('javascript:');
  });

  test('a hostile stored description cannot run script when the editor loads it', async ({ page }) => {
    const errors = collectPageErrors(page);
    // /api/v1/books/<id>/metadata returns the stored description RAW on purpose
    // (you edit what is stored), so anything an edit-capable user or a metadata
    // provider put there arrives untrusted. The <textarea> this replaced was
    // inert; innerHTML is not. <img onerror> DOES fire on an innerHTML write
    // even though <script> does not.
    await mockBook(page, '<p>Hi</p><img src=x onerror="window.__xss=1"><script>window.__xss2=1</script>');
    await page.goto(`/app/book/${TARGET_ID}/edit`);

    await expect(editor(page)).toContainText('Hi');
    expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
    expect(await page.evaluate(() => (window as unknown as { __xss2?: number }).__xss2)).toBeUndefined();
    await expect(editor(page).locator('img')).toHaveCount(0);
    assertNoPageErrors(errors);
  });

  test('a scheme hidden behind a tab is not treated as a relative link', async ({ page }) => {
    // Browsers strip tabs and newlines inside a URL, so "java<TAB>script:" runs.
    // A scheme regex that does not normalise first sees no scheme and lets it
    // through as a relative href.
    await mockBook(page, '');
    await page.goto(`/app/book/${TARGET_ID}/edit`);
    const box = editor(page);
    await box.click();
    await box.evaluate((el) => {
      const data = new DataTransfer();
      data.setData('text/html', '<a href="java\tscript:window.__xss3=1">click</a>');
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    });
    await expect(box).toContainText('click');
    await expect(box.locator('a')).toHaveCount(0);
  });

  test('HTML mode previews the markup as it is typed', async ({ page }) => {
    await mockBook(page, '');
    await page.goto(`/app/book/${TARGET_ID}/edit`);

    await page.getByRole('button', { name: 'Edit HTML' }).click();
    const source = page.getByRole('textbox', { name: 'Description' });
    await source.fill('<p>Live <strong>preview</strong> text.</p>');

    const preview = page.locator('[aria-labelledby$="-preview-label"]');
    await expect(preview).toContainText('Live preview text.');
    await expect(preview.locator('strong')).toHaveCount(1);

    await page.getByRole('button', { name: 'Back to formatting' }).click();
    await expect(editor(page).locator('strong')).toHaveCount(1);
  });
});
