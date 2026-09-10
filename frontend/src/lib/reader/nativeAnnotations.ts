/** Display native highlights in the archive the reader actually opened.
 * A Kobo CFI describes the KEPUB; the SPA normally opens the original EPUB.
 * Keep source anchors on the server intact. Verify native coordinates in this
 * document first; a unique exact quote is the conservative fallback when the
 * EPUB has no KoboSpans. Neither path changes formats or stored bookmarks.
 */
export interface NativeAnnotation {
  annotation_id: string;
  highlighted_text: string | null;
  start_kobospan?: string | null;
  end_kobospan?: string | null;
  content_id?: string | null;
  position_type?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
}

interface Section {
  url: string;
  cfiFromRange: (range: Range) => string;
}

interface Book {
  spine: { spineItems: Section[] };
  load: (url: string) => Promise<Document>;
}

export function hasNativeAnchor(row: NativeAnnotation): boolean {
  if (row.position_type && ['cfi', 'pdf_quad', 'comic_page', 'unanchored', 'koreader_xpointer'].includes(row.position_type)) return false;
  return !!(row.start_kobospan && row.end_kobospan);
}

function archivePaths(value: string, fromUri = false): string[] {
  if (!value || /^[a-z][a-z\d+.-]*:|^\/\//i.test(value) || /[\\\0]/.test(value)) return [];
  const variants = fromUri ? [] : [value];
  try { variants.push(decodeURIComponent(value)); } catch { /* retain only raw native names */ }
  return [...new Set(variants.flatMap((variant) => {
    if (/[\\\0]/.test(variant)) return [];
    const parts: string[] = [];
    for (const part of variant.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') { if (!parts.length) return []; parts.pop(); }
      else parts.push(part);
    }
    return parts.length ? [parts.join('/')] : [];
  }))];
}

function nativeSection(sections: Section[], contentId?: string | null): number | null {
  const chapter = contentId?.includes('!!') ? contentId.slice(contentId.indexOf('!!') + 2) : '';
  const candidates = archivePaths(chapter);
  if (!candidates.length) return null;
  const paths = sections.map((section) => {
    let url = section.url;
    if (/^[a-z][a-z\d+.-]*:/i.test(url)) {
      try { url = new URL(url).pathname; } catch { return []; }
    }
    // Section URLs are URIs, not native ZIP names: decode exactly once.
    return archivePaths(url, true);
  });
  let matches = paths.flatMap((members, index) => members.some((member) => candidates.includes(member)) ? [index] : []);
  if (!matches.length) matches = paths.flatMap((members, index) =>
    members.some((member) => candidates.some((candidate) => member.endsWith('/' + candidate))) ? [index] : []);
  return matches.length === 1 ? matches[0] : null;
}

function nativeRange(document: Document, row: NativeAnnotation): Range | null {
  const point = (id: string | null | undefined, offset: number | null | undefined) => {
    if (!id || !Number.isInteger(offset) || offset! < 0) return null;
    const elements = document.querySelectorAll(`[id="${CSS.escape(id)}"]`);
    if (elements.length !== 1 || !elements[0].closest('body')
      || elements[0].closest('script, style, noscript')) return null;
    const walker = document.createTreeWalker(elements[0], 4 /* SHOW_TEXT */);
    let remaining = offset!;
    let node: Node | null;
    while ((node = walker.nextNode())) {
      const length = (node as Text).length;
      if (remaining <= length) return { node, offset: remaining };
      remaining -= length;
    }
    return null;
  };
  const start = point(row.start_kobospan, row.start_offset);
  const end = point(row.end_kobospan, row.end_offset);
  if (!start || !end) return null;
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  return !range.collapsed && range.toString() === row.highlighted_text ? range : null;
}

/** Text nodes in document order, excluding non-reading content. DOM offsets and
 * JS string lengths both use UTF-16, including when a selection crosses emoji.
 */
function readingText(document: Document): { nodes: Text[]; text: string } {
  const body = document.querySelector('body');
  if (!body) throw new Error('Missing chapter body');
  const walker = document.createTreeWalker(body, 4 /* SHOW_TEXT */);
  const nodes: Text[] = [];
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (!node.parentElement?.closest('script, style, noscript')) nodes.push(node as Text);
  }
  return { nodes, text: nodes.map((node) => node.data).join('') };
}

function exactRange(document: Document, nodes: Text[], start: number, end: number): Range | null {
  const range = document.createRange();
  let offset = 0;
  let started = false;
  for (const node of nodes) {
    const next = offset + node.length;
    if (!started && start < next) {
      range.setStart(node, start - offset);
      started = true;
    }
    if (started && end <= next) {
      range.setEnd(node, end - offset);
      return range;
    }
    offset = next;
  }
  return null;
}

export async function resolveNativeAnnotations(
  book: Book, rows: NativeAnnotation[], cancelled: () => boolean = () => false,
): Promise<Map<string, string>> {
  const quotes = new Map<string, { count: number; cfi: string | null }>();
  for (const row of rows) {
    if (hasNativeAnchor(row) && row.highlighted_text?.trim()) {
      quotes.set(row.highlighted_text, { count: 0, cfi: null });
    }
  }
  const resolved = new Map<string, string>();
  if (!quotes.size) return resolved;
  const sections = book.spine.spineItems;
  const nativeSections = new Map(rows.filter(hasNativeAnchor).map((row) =>
    [row.annotation_id, nativeSection(sections, row.content_id)]));
  let complete = true;
  try {
    for (const [index, section] of sections.entries()) {
      if (cancelled()) return new Map();
      // Use a separate parsed document: search and rendition both own the
      // Section's cached DOM, so unloading/mutating it here would race them.
      let document: Document;
      let nodes: Text[];
      let text: string;
      try {
        document = await book.load(section.url);
        ({ nodes, text } = readingText(document));
      } catch { complete = false; continue; }
      if (cancelled()) return new Map();
      for (const row of rows) {
        if (nativeSections.get(row.annotation_id) !== index) continue;
        try {
          const range = nativeRange(document, row);
          if (range) resolved.set(row.annotation_id, section.cfiFromRange(range));
        } catch { /* An invalid native position can still use the quote fallback. */ }
      }
      for (const [quote, match] of quotes) {
        if (match.count > 1) continue;
        let from = 0;
        let index: number;
        while ((index = text.indexOf(quote, from)) !== -1) {
          match.count += 1;
          if (match.count > 1) { match.cfi = null; break; }
          const range = exactRange(document, nodes, index, index + quote.length);
          if (!range || range.toString() !== quote) {
            match.count = 2;
            match.cfi = null;
            break;
          }
          try { match.cfi = section.cfiFromRange(range); } catch {
            match.count = 2;
            match.cfi = null;
            break;
          }
          from = index + 1; // Include overlapping occurrences, too.
        }
      }
      // Let page turns and book changes run between chapter scans.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  } catch {
    complete = false;
  }
  if (cancelled()) return new Map();
  // Direct native positions don't depend on an unrelated chapter loading.
  // Text fallback does require a complete uniqueness scan.
  if (!complete) return resolved;
  for (const row of rows) {
    if (resolved.has(row.annotation_id)) continue;
    if (!hasNativeAnchor(row) || !row.highlighted_text) continue;
    const match = quotes.get(row.highlighted_text);
    if (match?.count === 1 && match.cfi) resolved.set(row.annotation_id, match.cfi);
  }
  return resolved;
}
