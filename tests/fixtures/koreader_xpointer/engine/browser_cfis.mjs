// Ask real epub.js, in real Chromium, for the CFI of given words (ground truth
// for the CFI side, the way probe.lua is for the XPointer side).
//
// usage: FRONTEND_DIR=/abs/frontend node browser_cfis.mjs <book.epub> <requests.json> > out.json
//   FRONTEND_DIR: a checkout's frontend/ with `npm ci` done (playwright, epubjs, jszip).
//   requests.json: [{spine, k, start, end}, ...] -- spine = 0-based spine index;
//     k = index of the text node among the rendered <body>'s text nodes that are
//     not whitespace-only, in document order; start/end = UTF-16 offsets in it.
//   Prints [{...request, cfi, text}] where cfi is what epub.js's
//   contents.cfiFromRange() gives for that range and text is range.toString().
// The book is rendered the way the SPA reader does (ePub(ArrayBuffer), renderTo,
// paginated), so the DOM is the one epub.js builds from the browser's parser.
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(`${process.env.FRONTEND_DIR}/package.json`);
const { chromium } = require('playwright');
const [epubPath, requestsPath] = process.argv.slice(2);
const requests = JSON.parse(readFileSync(requestsPath, 'utf8'));

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setContent('<!DOCTYPE html><html><body><div id="viewer" style="width:800px;height:1000px"></div></body></html>');
await page.addScriptTag({ path: require.resolve('jszip/dist/jszip.min.js') });
await page.addScriptTag({ path: require.resolve('epubjs/dist/epub.min.js') });
const results = await page.evaluate(async ({ b64, requests }) => {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const book = ePub(bytes.buffer);
  await book.ready;
  const rendition = book.renderTo('viewer', { width: 800, height: 1000, flow: 'paginated' });
  const out = [];
  const bySpine = new Map();
  for (const r of requests) {
    if (!bySpine.has(r.spine)) bySpine.set(r.spine, []);
    bySpine.get(r.spine).push(r);
  }
  for (const [spine, rows] of bySpine) {
    await rendition.display(book.spine.get(spine).href);
    const contents = rendition.getContents().find(c => c.sectionIndex === spine)
      || rendition.getContents()[0];
    const doc = contents.document;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const solid = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (/[^ \t\n\r]/.test(n.data)) solid.push(n);
    }
    for (const r of rows) {
      const node = solid[r.k];
      if (!node) { out.push({ ...r, cfi: null, text: null }); continue; }
      const range = doc.createRange();
      range.setStart(node, r.start);
      range.setEnd(node, r.end);
      out.push({ ...r, cfi: contents.cfiFromRange(range), text: range.toString() });
    }
  }
  return out;
}, { b64: readFileSync(epubPath).toString('base64'), requests });
process.stdout.write(JSON.stringify(results));
await browser.close();
