/*
 * Description-editor HTML rules — fork #919 (also #1038, an anonymous
 * "switched back to the classic view" report naming this same gap).
 *
 * The classic edit page wires TinyMCE to the description field
 * (cps/static/js/edit_books.js initialises it on #comments). The New UI's edit
 * form shipped a plain <textarea>, so descriptions showed as literal HTML with
 * no way to format or preview them.
 *
 * THE CONSTRAINT THAT SHAPES THIS FILE: descriptions are sanitized server-side
 * on the way out (cps/clean_html.py clean_string, called from
 * cps/api/serializers.py). bleach ESCAPES a tag outside its allowlist rather
 * than dropping it, so formatting the server does not keep does not merely
 * vanish — it comes back as visible "&lt;u&gt;text&lt;/u&gt;" in the reader's
 * description. Measured against the shipped container (bleach 6.2.0):
 *
 *     clean_string('<u>x</u>')  ->  '&lt;u&gt;x&lt;/u&gt;'
 *     clean_string('<h2>x</h2>') ->  '<h2>x</h2>'
 *
 * So the editor offers only formatting that survives a round trip. Underline
 * and strikethrough have no button on purpose. The correspondence is pinned by
 * tests/unit/test_description_editor_allowlist_ssot.py, which round-trips this
 * allowlist through the real server sanitizer — a button can never outlive the
 * tag it emits.
 *
 * This module is NOT the security boundary. The server sanitizes on render
 * regardless of what a client posts; sanitizing here keeps pasted markup clean
 * and keeps stored HTML close to what will actually be displayed.
 */

/** Tags the editor emits and keeps when cleaning pasted markup.
 *  Every entry must survive cps/clean_html.py's allowlist. */
export const EDITOR_ALLOWED_TAGS: ReadonlySet<string> = new Set([
  'p', 'br',
  'strong', 'em',
  'h2', 'h3', 'h4',
  'ul', 'ol', 'li',
  'blockquote',
  'code', 'pre',
  'a',
]);

/** Attributes kept, per tag. Mirrors bleach's ALLOWED_ATTRIBUTES for the tags
 *  we emit: everything else (style, class, id, target, rel) is stripped
 *  server-side, so keeping it here would only create a false preview. */
const ALLOWED_ATTRS: Record<string, readonly string[]> = {
  a: ['href', 'title'],
};

/** URL schemes bleach keeps (ALLOWED_PROTOCOLS). Relative URLs pass too. */
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

/** Elements whose contents are discarded outright rather than unwrapped. */
const DROP_SUBTREE = new Set([
  'script', 'style', 'head', 'meta', 'link', 'title',
  'iframe', 'object', 'embed', 'noscript', 'template', 'svg',
]);

/** Legacy/synonym tags normalised to the canonical one we emit, so output is
 *  stable across browsers (execCommand('bold') yields <b> in some engines and
 *  <strong> in others; both survive the server, only one is predictable). */
const TAG_ALIASES: Record<string, string> = {
  b: 'strong',
  i: 'em',
  strong: 'strong',
  em: 'em',
  div: 'p',
  section: 'p',
  article: 'p',
  h1: 'h2',
  h5: 'h4',
  h6: 'h4',
};

function isSafeUrl(raw: string): boolean {
  // Strip ASCII whitespace and control characters before looking for a scheme.
  // Browsers drop tabs and newlines inside a URL, so "java&#9;script:alert(1)"
  // resolves as javascript: while a naive scheme regex sees no scheme at all
  // and waves it through as a relative link.
  const url = raw.replace(/[\u0000-\u0020]/g, '');
  if (!url) return false;
  // Relative and anchor links carry no scheme and are left alone, matching
  // bleach (its protocol check only fires when a scheme is present).
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) {
    try {
      return ALLOWED_PROTOCOLS.has(new URL(url).protocol);
    } catch {
      return false;
    }
  }
  return true;
}

function cleanChildren(src: Node, doc: Document): Node[] {
  const out: Node[] = [];
  src.childNodes.forEach((child) => {
    if (child.nodeType === Node.TEXT_NODE) {
      const text = child.nodeValue ?? '';
      if (text) out.push(doc.createTextNode(text));
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return; // comments, PIs

    const el = child as Element;
    const tag = el.tagName.toLowerCase();
    if (DROP_SUBTREE.has(tag)) return;

    const mapped = TAG_ALIASES[tag] ?? tag;
    if (!EDITOR_ALLOWED_TAGS.has(mapped)) {
      // Unwrap rather than drop: a <span style> or <font> wrapper carries no
      // meaning once its attributes are stripped, but its text does. This is
      // what keeps a Goodreads/Amazon paste readable instead of empty.
      out.push(...cleanChildren(el, doc));
      return;
    }

    const clean = doc.createElement(mapped);
    for (const attr of ALLOWED_ATTRS[mapped] ?? []) {
      const value = el.getAttribute(attr);
      if (value === null) continue;
      if (attr === 'href' && !isSafeUrl(value)) continue;
      clean.setAttribute(attr, value);
    }
    // A link that lost its href is no longer a link; keep the text.
    if (mapped === 'a' && !clean.hasAttribute('href')) {
      out.push(...cleanChildren(el, doc));
      return;
    }
    cleanChildren(el, doc).forEach((node) => clean.appendChild(node));
    out.push(clean);
  });
  return out;
}

/**
 * Allowlist-clean a fragment of HTML down to what the server will keep.
 * Unknown elements are unwrapped (text preserved); disallowed attributes and
 * unsafe URL schemes are dropped.
 */
export function sanitizeDescriptionHtml(html: string): string {
  if (!html) return '';
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  const holder = doc.createElement('div');
  cleanChildren(doc.body, doc).forEach((node) => holder.appendChild(node));
  const result = holder.innerHTML;
  return isEmptyHtml(result) ? '' : result;
}

/**
 * True when the markup carries no text and no meaningful structure. A
 * contenteditable left empty reports "<br>" or "<p><br></p>" depending on the
 * browser, and storing that would turn "no description" into a blank paragraph
 * that renders as a stray gap on the book page.
 */
export function isEmptyHtml(html: string): boolean {
  if (!html) return true;
  const text = html
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/​/g, '')
    .trim();
  return text.length === 0;
}
