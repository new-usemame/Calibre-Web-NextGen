export interface SearchExcerptPart {
  text: string;
  matched: boolean;
}

interface LabelledHref {
  label: string;
  href: string;
}

/** Split book text without interpreting it as HTML or a regular expression. */
export function splitSearchExcerpt(excerpt: string, query: string): SearchExcerptPart[] {
  const needle = query.trim();
  if (!needle) return [{ text: excerpt, matched: false }];

  const lowerExcerpt = excerpt.toLocaleLowerCase();
  const lowerNeedle = needle.toLocaleLowerCase();
  const parts: SearchExcerptPart[] = [];
  let cursor = 0;
  let matchAt = lowerExcerpt.indexOf(lowerNeedle, cursor);

  while (matchAt !== -1) {
    if (matchAt > cursor) parts.push({ text: excerpt.slice(cursor, matchAt), matched: false });
    const end = matchAt + needle.length;
    parts.push({ text: excerpt.slice(matchAt, end), matched: true });
    cursor = end;
    matchAt = lowerExcerpt.indexOf(lowerNeedle, cursor);
  }

  if (cursor < excerpt.length) parts.push({ text: excerpt.slice(cursor), matched: false });
  return parts.length ? parts : [{ text: excerpt, matched: false }];
}

function comparableHref(href: string): string {
  const withoutFragment = href.split('#')[0].replace(/^\.\//, '');
  try { return decodeURIComponent(withoutFragment); } catch { return withoutFragment; }
}

/** Prefer a real TOC name, while always retaining the book's raw href fallback. */
export function chapterLabelForHref(href: string, toc: LabelledHref[]): string {
  const target = comparableHref(href);
  const item = toc.find((candidate) => {
    const tocHref = comparableHref(candidate.href);
    return target === tocHref || target.endsWith(`/${tocHref}`) || tocHref.endsWith(`/${target}`);
  });
  return item?.label.trim() || href;
}
