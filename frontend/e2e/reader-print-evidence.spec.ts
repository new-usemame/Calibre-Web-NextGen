import { expect, test } from '@playwright/test';
import JSZip from 'jszip';
import AxeBuilder from '@axe-core/playwright';

/** Synthetic raster evidence stays legible without recoloring ordinary art. */
async function evidenceBook(): Promise<Buffer> {
  const zip = new JSZip();
  zip.file('mimetype', 'application/epub+zip', { compression: 'STORE' });
  zip.file('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>');
  zip.file('book.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">synthetic-print-evidence</dc:identifier><dc:title>Print evidence</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-10-22T00:00:00Z</meta></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ink" href="ink.png" media-type="image/png"/></manifest><spine><itemref idref="chapter"/></spine></package>');
  zip.file('nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Print evidence</a></li></ol></nav></body></html>');
  zip.file('chapter.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Print evidence</title><style>img{width:80px;height:40px} .reflow-plausible{border-bottom:1px solid currentColor}</style></head><body><p>Readable <span class="reflow-flagged-reading"><span class="reflow-plausible">sample</span><a class="source-word-image" title="Inspect original print" href="#note"><img id="word-print" src="ink.png" alt=""/></a></span>.</p><img id="ordinary-art" src="ink.png" alt="Synthetic artwork"/><aside id="note" class="vision-disclosure"><p>Original print.</p><img id="detail-print" src="ink.png" alt="Original source evidence"/></aside></body></html>');
  // Two black columns on white paper, drawn for this test rather than OCR text.
  zip.file('ink.png', Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAIElEQVR4nGP8//8/AzbAhFUUIcEIBhAGcTqoIcGIy7kAoFAGEmozmo4AAAAASUVORK5CYII=', 'base64'));
  return zip.generateAsync({ type: 'nodebuffer' });
}

for (const theme of ['light', 'dark'] as const) {
  test(`real reader preserves print contrast in ${theme} without changing art`, async ({ page }) => {
    const book = await evidenceBook();
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 42, name: 'new-usemame', locale: 'en', theme, role: { anonymous: false, viewer: true, download: true }, features: {}, display: {} } });
      if (path.endsWith('/reader/settings')) return route.fulfill({ json: { reader: { theme: `${theme}Theme`, font: 'default', fontSize: 100, margin: 16, lineHeight: 150, spread: 'nonespread', reflow: true } } });
      if (path.endsWith('/reader/fonts')) return route.fulfill({ json: { items: [] } });
      if (path.endsWith('/books/8842')) return route.fulfill({ json: { id: 8842, title: 'Print evidence', authors: [], formats: [{ format: 'EPUB', size_bytes: book.length, content_url: '/show/8842/epub', download_url: '/download/8842/epub' }] } });
      if (path.includes('/bookmark')) return route.fulfill({ json: { cfi: null, percent: null } });
      if (path.includes('/annotations')) return route.fulfill({ json: { annotations: [], devices: {} } });
      return route.fulfill({ json: { items: [] } });
    });
    await page.route('**/show/8842/epub', route => route.fulfill({ contentType: 'application/epub+zip', body: book }));
    await page.goto('/app/read/8842');
    const frame = page.frameLocator('iframe[title="Book content"]').first();
    await expect(frame.locator('#word-print')).toBeVisible();
    await expect.poll(() => frame.locator('#word-print').evaluate(image => {
      const css = getComputedStyle(image);
      return [css.filter, css.mixBlendMode];
    })).toEqual([theme === 'dark' ? 'invert(1)' : 'none', theme === 'dark' ? 'screen' : 'multiply']);
    await expect.poll(() => frame.locator('#detail-print').evaluate(image => {
      const css = getComputedStyle(image);
      return [css.filter, css.mixBlendMode];
    })).toEqual([theme === 'dark' ? 'invert(1)' : 'none', theme === 'dark' ? 'screen' : 'multiply']);
    await expect(frame.locator('#ordinary-art')).toHaveCSS('filter', 'none');
    await expect(frame.locator('#ordinary-art')).toHaveCSS('mix-blend-mode', 'normal');
    await expect(frame.getByRole('link', { name: 'Inspect original print' })).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
  });
}
