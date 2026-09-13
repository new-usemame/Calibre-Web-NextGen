/*
 * In-book link routing for the web reader.
 *
 * WHY THIS EXISTS (measured 2026-09-12 against the real container, WebKit and
 * Chromium, with a fixture EPUB whose markup is the EPUB 3 footnote shape):
 *
 * epub.js renders every section into an iframe with `sandbox="allow-same-origin"`
 * and no `allow-scripts`, and its only link handling is `link.onclick = …;
 * return false` (utils/replacements.js `replaceLinks`). In Chromium that handler
 * runs and the click is swallowed. In WebKit — desktop Safari AND iOS-class touch
 * alike — a scripting-disabled document is dispatched NO DOM events at all:
 * touchstart, pointerdown, mousedown and click never arrive, so nothing can call
 * preventDefault(), while the browser still performs the link's native
 * activation. The frame therefore navigates to `origin + <section path>#id`,
 * which is not a route this server serves, and the reader's content frame fills
 * with an error/app page. That is the reported defect: tapping a footnote marker
 * replaced the book with the library.
 *
 * The consequence for design: nothing installed INSIDE the book frame can be
 * relied on. Activation has to be taken in the PARENT document, which is exactly
 * where epub.js already paints its highlight marks pane. Reader.tsx therefore
 * overlays parent-side hit targets on the links of the visible page, and this
 * module holds every decision those targets need — classification, resolution
 * and note extraction — as functions that can be tested without a browser.
 *
 * Everything here speaks the open vocabularies rather than one publisher's
 * convention: EPUB 3 structural semantics (`epub:type="noteref"` /
 * `footnote|endnote|rearnote`) and the DPUB-ARIA equivalents
 * (`role="doc-noteref"` / `doc-footnote` / `doc-endnote`).
 */

/** What a raw `href` attribute in book content points at. */
export type ReaderLinkKind =
  /** http(s) — leaves the app entirely. */
  | 'external'
  /** A non-web scheme such as mailto: or tel: — handed to the platform. */
  | 'scheme'
  /** A path and/or fragment inside the EPUB archive. */
  | 'in-book';

const SCHEME_PATTERN = /^([a-z][a-z0-9+.-]*):/i;

/** Schemes worth handing to the platform. Anything else (javascript:, data:,
 *  blob:, file:) is refused rather than opened: book content is untrusted. */
const OPENABLE_SCHEMES = new Set(['http', 'https', 'mailto', 'tel', 'sms']);

function schemeOf(href: string): string | null {
  const match = SCHEME_PATTERN.exec(href);
  return match ? match[1].toLowerCase() : null;
}

export function classifyHref(raw: string | null | undefined): ReaderLinkKind {
  const href = (raw ?? '').trim();
  const scheme = schemeOf(href);
  if (!scheme) return 'in-book';
  return scheme === 'http' || scheme === 'https' ? 'external' : 'scheme';
}

/** True when the href may be opened outside the reader. Guards the parent
 *  window against `javascript:` and `data:` URLs carried by book content. */
export function isOpenableHref(raw: string | null | undefined): boolean {
  const scheme = schemeOf((raw ?? '').trim());
  return scheme !== null && OPENABLE_SCHEMES.has(scheme);
}

export interface InBookTarget {
  /** Origin-relative path of the target document, as the browser resolved it
   *  against the `<base>` epub.js injects. This is the shape epub.js's own
   *  `book.path.relative()` expects. */
  path: string;
  /** Fragment id without the '#'; '' when the link carries none. */
  hash: string;
  /** True when the link points into the document it was clicked in — the case
   *  a footnote popup can answer without loading anything. */
  sameDocument: boolean;
}

/**
 * Resolve a link that the browser has already resolved for us.
 *
 * `resolvedHref` is `anchor.href` (absolute, resolved against the injected
 * `<base>`) and `sectionHref` is that base. Both are read from the book frame's
 * DOM, which stays readable from the parent even in WebKit, where events do not.
 */
export function inBookTarget(
  resolvedHref: string,
  sectionHref: string,
): InBookTarget | null {
  try {
    const target = new URL(resolvedHref);
    const section = new URL(sectionHref);
    let hash = target.hash.replace(/^#/, '');
    try { hash = decodeURIComponent(hash); } catch { /* keep the raw fragment */ }
    return {
      path: target.pathname,
      hash,
      sameDocument: target.pathname === section.pathname,
    };
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ *
 * EPUB 3 structural semantics + DPUB-ARIA
 * ------------------------------------------------------------------ */

export interface LinkSemantics {
  /** The value of the namespaced `epub:type` attribute, if any. */
  epubType?: string | null;
  /** The value of `role`, if any. */
  role?: string | null;
  /** Lower-case tag name, for the `<aside>` convention. */
  tagName?: string | null;
}

const NOTEREF_EPUB_TYPES = new Set(['noteref']);
const NOTE_EPUB_TYPES = new Set(['footnote', 'endnote', 'rearnote', 'note']);
const NOTEREF_ROLES = new Set(['doc-noteref']);
const NOTE_ROLES = new Set(['doc-footnote', 'doc-endnote']);
const BACKLINK_EPUB_TYPES = new Set(['backlink']);
const BACKLINK_ROLES = new Set(['doc-backlink']);

/** `epub:type` and `role` are both space-separated token lists. */
export function semanticTokens(value: string | null | undefined): string[] {
  return (value ?? '').trim().toLowerCase().split(/\s+/).filter(Boolean);
}

function matches(value: string | null | undefined, vocabulary: Set<string>): boolean {
  return semanticTokens(value).some((token) => vocabulary.has(token));
}

/** Is this anchor a note reference — the marker a reader taps? */
export function isNoterefAnchor({ epubType, role }: LinkSemantics): boolean {
  return matches(epubType, NOTEREF_EPUB_TYPES) || matches(role, NOTEREF_ROLES);
}

/**
 * Is this element the note itself?
 *
 * The declared semantics come first. A bare `<aside>` counts as well, because
 * plenty of converted books put the note in an aside and declare the semantics
 * only on the marker — and the caller only asks this about an element a noteref
 * already pointed at, so the aside convention cannot capture unrelated content.
 */
export function isNoteElement({ epubType, role, tagName }: LinkSemantics): boolean {
  if (matches(epubType, NOTE_EPUB_TYPES) || matches(role, NOTE_ROLES)) return true;
  return (tagName ?? '').toLowerCase() === 'aside';
}

/** A link from the note back to the marker it belongs to. Both the declared
 *  semantics and the plain `href="#<noteref id>"` convention count, because
 *  most real books declare neither. */
export function isBacklink(
  { epubType, role }: LinkSemantics,
  rawHref: string | null | undefined,
  noterefId: string | null | undefined,
): boolean {
  if (matches(epubType, BACKLINK_EPUB_TYPES) || matches(role, BACKLINK_ROLES)) return true;
  const href = (rawHref ?? '').trim();
  if (!href.startsWith('#') || !noterefId) return false;
  return href.slice(1) === noterefId;
}

/* ------------------------------------------------------------------ *
 * Note sanitisation policy
 * ------------------------------------------------------------------ */

/**
 * Inline and block markup a note may keep.
 *
 * `a` is deliberately absent: a link inside the popup would be a second
 * navigation surface with none of the routing above, so anchors are unwrapped
 * to their text. `img` is absent too — a note popup that fetches a remote image
 * is a tracking pixel the reader never asked for, and footnote text does not
 * need one.
 */
export const NOTE_ALLOWED_TAGS: ReadonlySet<string> = new Set([
  'p', 'br', 'span', 'div', 'section', 'aside',
  'em', 'i', 'strong', 'b', 'small', 'sup', 'sub', 'u', 's',
  'cite', 'q', 'blockquote', 'code', 'pre', 'abbr', 'time',
  'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  'ruby', 'rt', 'rp',
]);

/** Elements whose CONTENTS are discarded rather than unwrapped. */
export const NOTE_DROPPED_SUBTREES: ReadonlySet<string> = new Set([
  'script', 'style', 'link', 'meta', 'title', 'noscript', 'template',
  'iframe', 'object', 'embed', 'svg', 'math', 'canvas', 'video', 'audio',
  'img', 'picture', 'source', 'form', 'input', 'button', 'select', 'textarea',
]);

const NOTE_ALLOWED_ATTRS: Record<string, ReadonlySet<string>> = {
  abbr: new Set(['title']),
  time: new Set(['datetime']),
};

export function isAllowedNoteTag(tagName: string): boolean {
  return NOTE_ALLOWED_TAGS.has(tagName.toLowerCase());
}

export function isDroppedNoteSubtree(tagName: string): boolean {
  return NOTE_DROPPED_SUBTREES.has(tagName.toLowerCase());
}

/**
 * Attributes a note element may keep.
 *
 * Written as an allowlist per tag rather than a denylist of `on*`, so a handler
 * attribute nobody thought of (`onbeforetoggle`, the next one) is refused by
 * construction rather than by having been enumerated.
 */
export function isAllowedNoteAttribute(tagName: string, attribute: string): boolean {
  return NOTE_ALLOWED_ATTRS[tagName.toLowerCase()]?.has(attribute.toLowerCase()) ?? false;
}

/**
 * Rebuild `source` as markup that is safe to place in the app's own document.
 *
 * Rebuild, not clean-in-place: only allowlisted elements and attributes are
 * ever created, so nothing from the book survives by not having been noticed.
 * Construction happens in an inert document (no browsing context), so an
 * `onerror` image or a script in the note cannot run even while being rejected.
 *
 * @param noterefId id of the marker that opened the note, so its backlink can go.
 */
export function sanitizeNoteElement(source: Element, noterefId?: string | null): string {
  const inert = document.implementation.createHTMLDocument('note');

  const copyInto = (from: Node, into: Node): void => {
    from.childNodes.forEach((child) => {
      if (child.nodeType === 3 /* text */) {
        into.appendChild(inert.createTextNode(child.nodeValue ?? ''));
        return;
      }
      if (child.nodeType !== 1 /* element */) return;
      const element = child as Element;
      const tag = element.tagName.toLowerCase();
      if (isDroppedNoteSubtree(tag)) return;
      if (tag === 'a' && isBacklink(
        { epubType: element.getAttribute('epub:type'), role: element.getAttribute('role') },
        element.getAttribute('href'),
        noterefId,
      )) return;
      if (!isAllowedNoteTag(tag)) {
        // Unknown but harmless wrapper (an anchor, a <font>): keep the words.
        copyInto(element, into);
        return;
      }
      const copy = inert.createElement(tag);
      for (const attribute of Array.from(element.attributes)) {
        if (isAllowedNoteAttribute(tag, attribute.name)) {
          copy.setAttribute(attribute.name, attribute.value);
        }
      }
      copyInto(element, copy);
      into.appendChild(copy);
    });
  };

  copyInto(source, inert.body);
  return inert.body.innerHTML;
}
