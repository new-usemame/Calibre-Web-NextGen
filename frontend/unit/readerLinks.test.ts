import assert from 'node:assert/strict';
import test from 'node:test';
import {
  classifyHref,
  inBookTarget,
  isAllowedNoteAttribute,
  isAllowedNoteTag,
  isBacklink,
  isDroppedNoteSubtree,
  isNoteElement,
  isNoterefAnchor,
  isOpenableHref,
} from '../src/lib/readerLinks.ts';

/* The reader has to tell three populations apart before it can route anything:
 * links that leave the app, links the platform owns, and links into the book.
 * Getting this wrong is what sent the content frame to the library home page. */
test('a link is classified by its scheme, and book content cannot hand the app a javascript: URL', () => {
  assert.equal(classifyHref('#fn-80-2'), 'in-book');
  assert.equal(classifyHref('ch2.xhtml#fn-80-2'), 'in-book');
  assert.equal(classifyHref('../text/ch2.xhtml'), 'in-book');
  assert.equal(classifyHref(''), 'in-book');
  assert.equal(classifyHref('https://example.org/'), 'external');
  assert.equal(classifyHref('HTTPS://EXAMPLE.ORG/'), 'external');
  assert.equal(classifyHref('mailto:someone@example.org'), 'scheme');
  assert.equal(classifyHref('tel:+15551234567'), 'scheme');
  assert.equal(classifyHref('javascript:alert(1)'), 'scheme');

  assert.equal(isOpenableHref('mailto:someone@example.org'), true);
  assert.equal(isOpenableHref('tel:+15551234567'), true);
  assert.equal(isOpenableHref('https://example.org/'), true);
  // The three that must never reach window.open from untrusted book markup.
  assert.equal(isOpenableHref('javascript:alert(1)'), false);
  assert.equal(isOpenableHref('data:text/html,<script>alert(1)</script>'), false);
  assert.equal(isOpenableHref('file:///etc/passwd'), false);
  assert.equal(isOpenableHref('#fn-80-2'), false);
});

/* sameDocument is the decision that separates "show the note here" from "load
 * another section", and the path is what epub.js's display() is given. */
test('an in-book link resolves to a section path, a fragment, and whether it stayed in this document', () => {
  const section = 'http://reader.example/OEBPS/text/ch1.xhtml';

  assert.deepEqual(inBookTarget('http://reader.example/OEBPS/text/ch1.xhtml#fn-80-2', section),
    { path: '/OEBPS/text/ch1.xhtml', hash: 'fn-80-2', sameDocument: true });

  assert.deepEqual(inBookTarget('http://reader.example/OEBPS/text/ch2.xhtml#fn-cross', section),
    { path: '/OEBPS/text/ch2.xhtml', hash: 'fn-cross', sameDocument: false });

  // A plain cross-document link carries no fragment.
  assert.deepEqual(inBookTarget('http://reader.example/OEBPS/text/ch2.xhtml', section),
    { path: '/OEBPS/text/ch2.xhtml', hash: '', sameDocument: false });

  // Fragments in non-ASCII books arrive percent-encoded from anchor.href and
  // have to be decoded before getElementById can find the note.
  assert.deepEqual(inBookTarget('http://reader.example/OEBPS/text/ch1.xhtml#note-%C3%A9', section),
    { path: '/OEBPS/text/ch1.xhtml', hash: 'note-é', sameDocument: true });

  assert.equal(inBookTarget('not a url', section), null);
});

test('a note reference is recognised through EPUB 3 semantics or its DPUB-ARIA equivalent', () => {
  assert.equal(isNoterefAnchor({ epubType: 'noteref' }), true);
  assert.equal(isNoterefAnchor({ role: 'doc-noteref' }), true);
  // Token lists, and the casing real books ship.
  assert.equal(isNoterefAnchor({ epubType: 'backlink noteref' }), true);
  assert.equal(isNoterefAnchor({ epubType: 'NoteRef' }), true);
  // An ordinary cross-reference must NOT open a popup.
  assert.equal(isNoterefAnchor({ epubType: null, role: null }), false);
  assert.equal(isNoterefAnchor({ epubType: 'bodymatter', role: 'link' }), false);
});

test('the note itself is recognised by declared semantics, or by the aside convention', () => {
  assert.equal(isNoteElement({ epubType: 'footnote', tagName: 'aside' }), true);
  assert.equal(isNoteElement({ epubType: 'endnote', tagName: 'li' }), true);
  assert.equal(isNoteElement({ epubType: 'rearnote', tagName: 'div' }), true);
  assert.equal(isNoteElement({ role: 'doc-footnote', tagName: 'div' }), true);
  assert.equal(isNoteElement({ role: 'doc-endnote', tagName: 'li' }), true);
  // Undeclared aside: the marker already said "noteref", so honour it.
  assert.equal(isNoteElement({ tagName: 'aside' }), true);
  // A heading the marker happens to point at is a place to go, not a note.
  assert.equal(isNoteElement({ tagName: 'h2' }), false);
  assert.equal(isNoteElement({ epubType: 'chapter', tagName: 'section' }), false);
});

test('the note’s link back to its marker is dropped, and an unrelated link in the note is not', () => {
  assert.equal(isBacklink({}, '#fnref-80-2', 'fnref-80-2'), true);
  assert.equal(isBacklink({ epubType: 'backlink' }, 'ch1.xhtml#fnref-80-2', 'fnref-80-2'), true);
  assert.equal(isBacklink({ role: 'doc-backlink' }, 'whatever', 'fnref-80-2'), true);
  assert.equal(isBacklink({}, '#fnref-99-1', 'fnref-80-2'), false);
  assert.equal(isBacklink({}, 'https://example.org/source', 'fnref-80-2'), false);
  assert.equal(isBacklink({}, '#fnref-80-2', null), false);
});

/* The popup puts book markup into the APP's document, where scripting is on.
 * The policy is an allowlist so an event-handler attribute nobody enumerated is
 * refused by construction. */
test('note markup keeps text formatting and can never carry script or an event handler', () => {
  assert.equal(isAllowedNoteTag('p'), true);
  assert.equal(isAllowedNoteTag('EM'), true);
  assert.equal(isAllowedNoteTag('sup'), true);
  assert.equal(isAllowedNoteTag('script'), false);
  assert.equal(isAllowedNoteTag('img'), false);
  assert.equal(isAllowedNoteTag('a'), false);

  assert.equal(isDroppedNoteSubtree('script'), true);
  assert.equal(isDroppedNoteSubtree('style'), true);
  assert.equal(isDroppedNoteSubtree('iframe'), true);
  assert.equal(isDroppedNoteSubtree('img'), true);
  assert.equal(isDroppedNoteSubtree('p'), false);

  for (const tag of ['p', 'span', 'em', 'abbr', 'time', 'blockquote']) {
    for (const attribute of ['onclick', 'onerror', 'onbeforetoggle', 'style', 'srcdoc', 'href', 'src']) {
      assert.equal(isAllowedNoteAttribute(tag, attribute), false, `${tag}[${attribute}]`);
    }
  }
  assert.equal(isAllowedNoteAttribute('abbr', 'title'), true);
  assert.equal(isAllowedNoteAttribute('time', 'datetime'), true);
  assert.equal(isAllowedNoteAttribute('ABBR', 'TITLE'), true);
});
