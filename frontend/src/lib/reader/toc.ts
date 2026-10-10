/** One table-of-contents entry, with the entries nested under it. */
export interface TocItem {
  label: string;
  href: string;
  subitems: TocItem[];
}

/**
 * The reader's table of contents from epub.js's `navigation.toc`.
 *
 * epub.js parses both an EPUB 3 nav document and an EPUB 2 NCX into a tree:
 * a Part holds its Chapters in `subitems`, a Chapter its Sections. Keeping
 * only the top level (#2253) left a multi-level book navigable by Part alone.
 */
export function tocFromNavigation(items: unknown, navPath?: string, packageDir?: string): TocItem[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item): item is { label?: unknown; href?: unknown; subitems?: unknown } =>
      !!item && typeof item === 'object')
    .map((item) => ({
      label: typeof item.label === 'string' ? item.label.trim() : '',
      href: typeof item.href === 'string' ? packageRelativeHref(item.href, navPath, packageDir) : '',
      subitems: tocFromNavigation(item.subitems, navPath, packageDir),
    }));
}

/**
 * A TOC href is written relative to the navigation document, but epub.js keys
 * the spine relative to the package document. The two agree while both files
 * share a folder; a nav document elsewhere (`../nav.xhtml` beside
 * `OEBPS/content.opf`, with entries like `OEBPS/ch1.html`) misses the spine on
 * every entry, and choosing one did nothing. `navPath` is the manifest href of
 * the nav document or NCX, itself relative to the package document, and
 * `packageDir` the package document's folder (epub.js `book.path.directory`).
 */
export function packageRelativeHref(href: string, navPath?: string, packageDir = '/'): string {
  if (!href || !navPath || !navPath.includes('/')) return href;
  let root: URL;
  let resolved: URL;
  try {
    root = new URL(packageDir, 'https://book.invalid/');
    resolved = new URL(href, new URL(navPath, root));
  } catch {
    return href;
  }
  if (resolved.origin !== root.origin || !resolved.pathname.startsWith(root.pathname)) return href;
  let path = resolved.pathname.slice(root.pathname.length);
  try { path = decodeURI(path); } catch { /* keep it encoded */ }
  return path + resolved.hash;
}

/** Every entry in reading order, parents before their children. */
export function flattenToc(items: TocItem[]): TocItem[] {
  return items.flatMap((item) => [item, ...flattenToc(item.subitems)]);
}
