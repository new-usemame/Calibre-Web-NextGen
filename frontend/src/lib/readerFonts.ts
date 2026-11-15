export interface ReaderFont {
  id: string;
  label: string;
  family: string;
  builtin: boolean;
  url?: string;
  format?: string;
}

/** Existing built-ins remain usable if the optional catalog request fails. */
export const BUILTIN_READER_FONTS: ReaderFont[] = [
  { id: 'default', label: 'Book default', family: 'initial', builtin: true },
  { id: 'Literata', label: 'Literata', family: "'Literata', serif", builtin: true },
  { id: 'Arial', label: 'Arial', family: 'Arial, Helvetica, sans-serif', builtin: true },
  { id: 'Yahei', label: 'Microsoft YaHei', family: '"Microsoft YaHei", sans-serif', builtin: true },
  { id: 'SimSun', label: 'SimSun', family: 'SimSun, serif', builtin: true },
  { id: 'KaiTi', label: 'KaiTi', family: 'KaiTi, serif', builtin: true },
];

export interface ReaderFontCatalog {
  items: ReaderFont[];
  limits: { max_file_bytes: number; max_fonts: number; max_total_bytes: number };
}

/** Only server-generated families and same-origin font URLs enter a stylesheet.
 * Labels remain ordinary text and never become CSS identifiers. */
export function readerFontFaceCss(items: ReaderFont[], origin: string): string {
  return items.flatMap(font => {
    if (!font.url || !/^[A-Za-z][A-Za-z0-9_-]{0,80}$/.test(font.family)
      || !['woff', 'woff2', 'truetype', 'opentype'].includes(font.format ?? '')) return [];
    try {
      const url = new URL(font.url, origin);
      if (url.origin !== new URL(origin).origin || !['http:', 'https:'].includes(url.protocol)) return [];
      return [`@font-face{font-family:${font.family};font-style:normal;font-weight:normal;` +
        `font-display:swap;src:url(${JSON.stringify(url.href)}) format('${font.format}');}`];
    } catch { return []; }
  }).join('');
}

export function readerFontFamily(items: ReaderFont[], id: string): string {
  return items.find(font => font.id === id)?.family || 'initial';
}
