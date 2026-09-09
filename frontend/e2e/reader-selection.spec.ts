import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const builder = fileURLToPath(new URL('../../tests/fixtures/reader_archive.py', import.meta.url));
const installation = 'f39f3223-6d1c-48be-8aa3-95a88d9ac0c1';

async function openReader(page: Page, classic: boolean) {
  await page.addInitScript((installation) => {
    if (window === window.top) (window as any).__readerBookScriptRan = false;
    localStorage.setItem('cwng.webreader.installation-id.v1', installation);
    window.addEventListener('locationchange', () => {
      if ((window as any).epub?.locations?.length() > 0) (window as any).__classicPositionReady = true;
    });
  }, installation);
  const catalog = await (await page.request.get('/api/v1/books?per_page=100')).json();
  const id = catalog.items.find((book: any) => book.formats?.some((format: string) => format.toLowerCase() === 'epub')).id;
  const bytes = execFileSync('python3', [builder], { input: JSON.stringify([
    '<p><span id="kobo.1.1">Selectable passage for a brand new highlight.</span></p>' +
      '<script>window.parent.__readerBookScriptRan = true;</script>',
  ]) });
  await page.route(`**/api/v1/books/${id}`, async (route) => {
    const response = await route.fetch(); const detail = await response.json();
    detail.formats = [{ format: 'EPUB', content_url: `/show/${id}/epub`, size_bytes: bytes.length }];
    await route.fulfill({ response, json: detail });
  });
  await page.route(`**/show/${id}/**`, (route) => route.fulfill({ contentType: 'application/epub+zip', body: bytes }));
  await page.route(`**/annotations/${id}/data.json`, (route) => route.fulfill({ json: { annotations: [], devices: {} } }));
  await page.route(`**/api/v1/books/${id}/bookmark*`, (route) => route.fulfill({ json: { bookmark: null } }));
  await page.route(`**/ajax/bookmark/${id}/*`, (route) => route.fulfill({ json: {} }));
  const writes: { body: any; installation?: string }[] = [];
  await page.route(`**/annotations/${id}`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const body = route.request().postDataJSON();
    writes.push({ body, installation: route.request().headers()['x-cwng-webreader-installation-id'] });
    await route.fulfill({ status: 201, json: { ...body, annotation_id: 'selection-created',
      content_id: 'fixture!!OPS/part0/chapter.xhtml', position_type: classic ? null : 'cfi' } });
  });
  await page.goto(classic ? `/read/${id}/epub` : `/app/read/${id}`);
  if (classic) await page.waitForFunction(() => (window as any).reader?.SidebarController && (window as any).__classicPositionReady);
  await expect.poll(async () => {
    for (const frame of page.frames()) if (frame !== page.mainFrame() && await frame.locator('[id="kobo.1.1"]').count()) return true;
    return false;
  }).toBe(true);
  const frame = (await Promise.all(page.frames().map(async (frame) => ({ frame, matches: frame !== page.mainFrame() && await frame.locator('[id="kobo.1.1"]').count() })))).find((item) => item.matches)!.frame;
  return { frame, writes };
}

for (const classic of [false, true]) {
  test(`${classic ? 'classic' : 'SPA'} new text selection creates a highlight without enabling book scripts`, async ({ page, isMobile, browserName }) => {
    const { frame, writes } = await openReader(page, classic);
    const element = await frame.frameElement();
    expect(await element.getAttribute('sandbox')).not.toContain('allow-scripts');
    if (isMobile) {
      // WebKit's mobile profile has no OS selection handles. Preserve real
      // iframe focus and DOM selection; the parent observer must discover it.
      await frame.evaluate(() => {
        window.focus(); const node = document.getElementById('kobo.1.1')!.firstChild!;
        const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, 6);
        const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
      });
    } else {
      const frameBox = (await element.boundingBox())!;
      const textBox = await frame.evaluate(() => {
        const node = document.getElementById('kobo.1.1')!.firstChild!;
        const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, 6);
        const box = range.getBoundingClientRect(); return { x: box.x, y: box.y, width: box.width, height: box.height };
      });
      const start = { x: frameBox.x + textBox.x + 1, y: frameBox.y + textBox.y + textBox.height / 2 };
      const end = { x: frameBox.x + textBox.x + textBox.width - 1, y: start.y };
      if (browserName === 'chromium') {
        // Playwright's drag interception injects timer-based listeners into
        // the script-disabled frame. Send trusted browser input directly.
        const cdp = await page.context().newCDPSession(page);
        try {
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...start, buttons: 0 });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...start, button: 'left', buttons: 1, clickCount: 1 });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...end, button: 'left', buttons: 1 });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...end, button: 'left', buttons: 0, clickCount: 1 });
        } finally { await cdp.detach(); }
      } else {
        await page.mouse.move(start.x, start.y);
        await page.mouse.down();
        await page.mouse.move(end.x, end.y, { steps: 12 });
        await page.mouse.up();
      }
    }
    const selected = await frame.evaluate(() => window.getSelection()?.toString());
    expect(selected).toBe('Select');
    if (classic) {
      await expect(page.locator('.cwa-ann-save')).toBeVisible();
      await page.locator('.cwa-ann-save').click();
    } else {
      const dialog = page.getByRole('dialog', { name: 'Highlight color', exact: true });
      await expect(dialog).toBeVisible();
      await dialog.getByRole('button', { name: 'Yellow', exact: true }).click();
    }
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].installation).toBe(installation);
    expect(writes[0].body.highlighted_text).toBe('Select');
    if (classic) {
      expect(writes[0].body).toMatchObject({ chapter_filename: 'part0/chapter.xhtml', start_kobospan: 'kobo.1.1', end_kobospan: 'kobo.1.1', start_offset: 0, end_offset: 6 });
      await expect(page.locator('[data-annotation-id="selection-created"]')).toBeAttached();
    } else {
      expect(writes[0].body.cfi_range).toMatch(/^epubcfi\(/);
      await expect(page.locator('[data-id="selection-created"] rect').first()).toBeAttached();
    }
    expect(await page.evaluate(() => (window as any).__readerBookScriptRan)).toBe(false);
    expect(await element.getAttribute('sandbox')).not.toContain('allow-scripts');
  });
}
