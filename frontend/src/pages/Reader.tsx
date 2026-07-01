import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'wouter';
import ePub from 'epubjs';
import {
  ChevronLeft, ChevronRight, X, List, Type, Sun, Moon, Coffee, Loader2,
} from 'lucide-react';
import { useBook, useBookmark, useSaveBookmark } from '../lib/queries';
import { apiPost, apiUrl, resourceUrl } from '../lib/api';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Reader.module.css';

// Highlight colors (match the legacy/Kobo set). Rendered semi-transparent.
const HILITE_FILL: Record<string, string> = {
  yellow: '#e6c34a', red: '#d9534f', green: '#5cb85c', blue: '#5b9bd5',
};

type ReaderTheme = 'light' | 'sepia' | 'dark';

interface TocItem {
  label: string;
  href: string;
}

// epub.js ships loose types; the rendition/book objects are treated as `any`
// behind small typed wrappers so the rest of the component stays readable.
/* eslint-disable @typescript-eslint/no-explicit-any */

// !important on the body rules so a theme switch always wins over the book's own
// CSS and any previously-selected theme (without it, re-selecting a theme epub.js
// considers "already applied" can leave the prior background showing).
const THEMES: Record<ReaderTheme, { body: Record<string, string> }> = {
  light: { body: { background: '#fbf7ee !important', color: '#2a2a2a !important' } },
  sepia: { body: { background: '#f2e6cf !important', color: '#43381f !important' } },
  dark: { body: { background: '#15110c !important', color: '#cdc6bb !important' } },
};

const FONT_MIN = 80;
const FONT_MAX = 160;
const LS_THEME = 'cwng.reader.theme';
const LS_FONT = 'cwng.reader.font';

function loadTheme(): ReaderTheme {
  const v = localStorage.getItem(LS_THEME);
  return v === 'light' || v === 'sepia' || v === 'dark' ? v : 'dark';
}
function loadFont(): number {
  const v = Number(localStorage.getItem(LS_FONT));
  return v >= FONT_MIN && v <= FONT_MAX ? v : 100;
}

export function Reader({ id }: { id: string }) {
  const t = useT();
  const { data: book, isLoading, error } = useBook(id);
  const { data: savedBookmark } = useBookmark(id, 'epub');
  const saveBookmark = useSaveBookmark(id);

  const viewerRef = useRef<HTMLDivElement>(null);
  const renditionRef = useRef<any>(null);
  const bookRef = useRef<any>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Hold the freshest saved CFI so it survives re-renders without re-running the effect.
  const savedCfiRef = useRef<string | null>(null);

  const [rendered, setRendered] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [toc, setToc] = useState<TocItem[]>([]);
  const [tocOpen, setTocOpen] = useState(false);
  const [theme, setTheme] = useState<ReaderTheme>(loadTheme);
  const [fontPct, setFontPct] = useState(loadFont);
  const [progress, setProgress] = useState(0);
  // Pending text selection awaiting a highlight-color choice.
  const [pendingSel, setPendingSel] = useState<{ cfiRange: string; text: string } | null>(null);

  const epubFormat = book?.formats.find((f) => f.format.toLowerCase() === 'epub');

  // Paint a highlight onto the live rendition (epub.js annotations API).
  const paintHighlight = useCallback((cfiRange: string, color: string) => {
    try {
      renditionRef.current?.annotations?.highlight(
        cfiRange, {}, undefined, '',
        { fill: HILITE_FILL[color] || HILITE_FILL.yellow, 'fill-opacity': '0.35' },
      );
    } catch { /* epub.js throws on a stale/foreign CFI — ignore */ }
  }, []);

  // Create a highlight from the pending selection, persist it, paint it.
  const createHighlight = useCallback(async (color: string) => {
    const sel = pendingSel;
    if (!sel) return;
    setPendingSel(null);
    try {
      await apiPost(`/annotations/${id}`, {
        cfi_range: sel.cfiRange, highlighted_text: sel.text, highlight_color: color,
      });
      paintHighlight(sel.cfiRange, color);
    } catch { /* surfaced as no-op; user can retry */ }
    try {
      (renditionRef.current?.getContents?.() || []).forEach((c: any) => c.window?.getSelection?.().removeAllRanges());
    } catch { /* noop */ }
  }, [pendingSel, id, paintHighlight]);

  useEffect(() => {
    savedCfiRef.current = savedBookmark?.bookmark ?? savedCfiRef.current;
  }, [savedBookmark]);

  const persistCfi = useCallback(
    (cfi: string) => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        saveBookmark.mutate({ format: 'epub', bookmark: cfi });
      }, 800);
    },
    [saveBookmark],
  );

  const applyTheme = useCallback((t: ReaderTheme) => {
    const rendition = renditionRef.current;
    if (!rendition) return;
    // Select the registered theme so future (page-turn) sections paint correctly…
    rendition.themes.select(t);
    // …and force it onto the currently-rendered iframe with inline styles, which
    // win unconditionally. epub.js can skip re-applying a theme it considers
    // already current (notably the initial 'dark'), leaving the prior background.
    const bg = THEMES[t].body.background.replace(' !important', '');
    const fg = THEMES[t].body.color.replace(' !important', '');
    // epub.js injects several equal-specificity `!important` body rules per theme;
    // the LAST one appended wins, so a previously-selected light/sepia rule beats
    // dark on re-select. An `!important` INLINE style sits above every stylesheet
    // rule in the cascade — set it with priority so the chosen theme always wins.
    try {
      (rendition.getContents?.() || []).forEach((c: any) => {
        if (!c?.document) return;
        c.document.documentElement.style.setProperty('background', bg, 'important');
        if (c.document.body) {
          c.document.body.style.setProperty('background', bg, 'important');
          c.document.body.style.setProperty('color', fg, 'important');
        }
      });
    } catch { /* same-origin blob content; guard regardless */ }
  }, []);

  // Build the rendition once the epub format + its download URL are known.
  useEffect(() => {
    if (!epubFormat || !viewerRef.current) return;
    let cancelled = false;
    setRendered(false);
    setRenderError(null);

    (async () => {
      try {
        // Fetch the .epub ourselves (same-origin cookie auth) and hand epub.js
        // an ArrayBuffer — reliable archive open regardless of the URL extension.
        const res = await fetch(resourceUrl(epubFormat.download_url), { credentials: 'include' });
        if (!res.ok) throw new Error(`Could not load the book file (${res.status})`);
        const buf = await res.arrayBuffer();
        if (cancelled) return;

        const epubBook = ePub(buf as any);
        bookRef.current = epubBook;
        const rendition = epubBook.renderTo(viewerRef.current!, {
          width: '100%',
          height: '100%',
          flow: 'paginated',
          spread: 'auto',
        });
        renditionRef.current = rendition;

        Object.entries(THEMES).forEach(([name, t]) => rendition.themes.register(name, t));
        rendition.themes.select(theme);
        rendition.themes.fontSize(`${fontPct}%`);

        await rendition.display(savedCfiRef.current || undefined);
        if (cancelled) return;
        setRendered(true);

        epubBook.loaded.navigation.then((nav: any) => {
          if (!cancelled) {
            setToc(nav.toc.map((t: any) => ({ label: (t.label || '').trim(), href: t.href })));
          }
        });

        // Lazily generate locations for a progress percentage.
        epubBook.ready
          .then(() => epubBook.locations.generate(1600))
          .then(() => {
            if (cancelled) return;
            const loc = rendition.currentLocation() as any;
            if (loc?.start?.cfi && epubBook.locations.length()) {
              setProgress(Math.round(epubBook.locations.percentageFromCfi(loc.start.cfi) * 100));
            }
          })
          .catch(() => {/* locations are best-effort */});

        rendition.on('relocated', (location: any) => {
          const cfi = location?.start?.cfi;
          if (!cfi) return;
          persistCfi(cfi);
          if (epubBook.locations.length()) {
            setProgress(Math.round(epubBook.locations.percentageFromCfi(cfi) * 100));
          }
        });

        // Render existing highlights (the CFI-anchored ones we can place).
        fetch(apiUrl(`/annotations/${id}/data.json`), { credentials: 'include' })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (cancelled || !d) return;
            (d.annotations || []).forEach((a: any) => {
              if (a.cfi_range) {
                try {
                  rendition.annotations.highlight(a.cfi_range, {}, undefined, '',
                    { fill: HILITE_FILL[a.highlight_color] || HILITE_FILL.yellow, 'fill-opacity': '0.35' });
                } catch { /* skip un-placeable CFI */ }
              }
            });
          })
          .catch(() => { /* highlights are best-effort */ });

        // Capture a text selection → offer a highlight-color popover.
        rendition.on('selected', (cfiRange: string, contents: any) => {
          let text = '';
          try { text = (contents?.window?.getSelection?.().toString() || '').trim(); } catch { /* noop */ }
          if (cfiRange) setPendingSel({ cfiRange, text });
        });
      } catch (e) {
        if (!cancelled) setRenderError(e instanceof Error ? e.message : 'Failed to open the book.');
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      try { renditionRef.current?.destroy(); } catch { /* noop */ }
      try { bookRef.current?.destroy(); } catch { /* noop */ }
      renditionRef.current = null;
      bookRef.current = null;
    };
    // Re-render only when the source changes; theme/font are applied imperatively.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epubFormat?.download_url]);

  // Apply theme / font changes to a live rendition without rebuilding it, and
  // remember the preference across sessions.
  useEffect(() => {
    localStorage.setItem(LS_THEME, theme);
    applyTheme(theme);
  }, [theme, applyTheme]);
  useEffect(() => {
    localStorage.setItem(LS_FONT, String(fontPct));
    renditionRef.current?.themes.fontSize(`${fontPct}%`);
  }, [fontPct]);

  const goPrev = useCallback(() => renditionRef.current?.prev(), []);
  const goNext = useCallback(() => renditionRef.current?.next(), []);

  // Arrow-key navigation (the iframe also forwards keys via rendition).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') goPrev();
      if (e.key === 'ArrowRight') goNext();
    };
    document.addEventListener('keyup', onKey);
    renditionRef.current?.on('keyup', onKey);
    return () => document.removeEventListener('keyup', onKey);
  }, [goPrev, goNext, rendered]);

  const goToc = (href: string) => {
    const rendition = renditionRef.current;
    const epubBook = bookRef.current;
    setTocOpen(false);
    if (!rendition) return;
    // Resolve the TOC href to a spine section first: epub.js's display(href) can
    // throw "No Section Found" when the toc href and spine href bases differ
    // (common when opening from an ArrayBuffer). spine.get() matches by href/id/
    // index and is robust; fall back to the raw href (sans fragment) if needed.
    let target: string | number = href;
    try {
      const section = epubBook?.spine?.get(href);
      if (section && typeof section.index === 'number') target = section.index;
    } catch { /* fall through to href */ }
    Promise.resolve(rendition.display(target)).catch(() => {
      Promise.resolve(rendition.display(href.split('#')[0])).catch(() => {/* give up quietly */});
    });
  };

  if (isLoading) {
    return (
      <div className={styles.fullCenter}>
        <Loader2 className={styles.spin} size={36} />
      </div>
    );
  }

  if (error || !book) {
    return (
      <div className={styles.fullCenter}>
        <EmptyState message={error instanceof Error ? error.message : t('Book not found.')} />
        <Link href="/" className={styles.exitLink}>{t('← Library')}</Link>
      </div>
    );
  }

  if (!epubFormat) {
    // No epub format — fall back to the legacy reader for other formats.
    const other = book.formats[0];
    return (
      <div className={styles.fullCenter}>
        <EmptyState message={t('In-browser reading currently supports EPUB. Use download or the classic reader for other formats.')} />
        <div className={styles.fallbackRow}>
          {other && <a className={styles.exitLink} href={resourceUrl(other.read_url)}>{t('Open classic reader')}</a>}
          <Link href={`/book/${id}`} className={styles.exitLink}>{t('← Back to book')}</Link>
        </div>
      </div>
    );
  }

  return (
    <div className={`${styles.reader} ${styles[`bg_${theme}`]}`}>
      {/* Top bar */}
      <header className={styles.bar}>
        <Link href={`/book/${id}`} className={styles.iconBtn} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={20} />
        </Link>
        <span className={styles.bookTitle}>{book.title}</span>
        <div className={styles.barControls}>
          <button className={styles.iconBtn} onClick={() => setTocOpen((o) => !o)} aria-label={t('Table of contents')} title={t('Contents')}>
            <List size={19} />
          </button>
          <div className={styles.fontControls}>
            <button className={styles.iconBtn} onClick={() => setFontPct((p) => Math.max(FONT_MIN, p - 10))} aria-label={t('Smaller text')} title={t('Smaller')}>
              <Type size={14} />
            </button>
            <button className={styles.iconBtn} onClick={() => setFontPct((p) => Math.min(FONT_MAX, p + 10))} aria-label={t('Larger text')} title={t('Larger')}>
              <Type size={20} />
            </button>
          </div>
          <div className={styles.themeControls}>
            <button className={theme === 'light' ? styles.themeActive : styles.iconBtn} onClick={() => setTheme('light')} aria-label={t('Light theme')} title={t('Light')}><Sun size={17} /></button>
            <button className={theme === 'sepia' ? styles.themeActive : styles.iconBtn} onClick={() => setTheme('sepia')} aria-label={t('Sepia theme')} title={t('Sepia')}><Coffee size={17} /></button>
            <button className={theme === 'dark' ? styles.themeActive : styles.iconBtn} onClick={() => setTheme('dark')} aria-label={t('Dark theme')} title={t('Dark')}><Moon size={17} /></button>
          </div>
        </div>
      </header>

      {/* TOC drawer */}
      {tocOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setTocOpen(false)} aria-hidden="true" />
          <nav className={styles.toc} aria-label={t('Table of contents')}>
            <p className={styles.tocHeading}>{t('Contents')}</p>
            {toc.length === 0 ? (
              <p className={styles.tocEmpty}>{t('No contents found.')}</p>
            ) : (
              <ul>
                {toc.map((tocItem, i) => (
                  <li key={`${tocItem.href}-${i}`}>
                    <button className={styles.tocItem} onClick={() => goToc(tocItem.href)}>{tocItem.label || t('Untitled')}</button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
        </>
      )}

      {/* Viewer + page-turn zones */}
      <div className={styles.stage}>
        <button className={`${styles.navZone} ${styles.navPrev}`} onClick={goPrev} aria-label={t('Previous page')}>
          <ChevronLeft size={28} />
        </button>
        <div ref={viewerRef} className={styles.viewer} />
        <button className={`${styles.navZone} ${styles.navNext}`} onClick={goNext} aria-label={t('Next page')}>
          <ChevronRight size={28} />
        </button>

        {!rendered && !renderError && (
          <div className={styles.viewerOverlay}>
            <Loader2 className={styles.spin} size={32} />
          </div>
        )}
        {renderError && (
          <div className={styles.viewerOverlay}>
            <EmptyState message={renderError} />
          </div>
        )}
      </div>

      {/* Highlight color popover for the current selection */}
      {pendingSel && (
        <div className={styles.hilitePop} role="dialog" aria-label={t('Highlight')}>
          <span className={styles.hiliteLabel}>{t('Highlight')}</span>
          {(['yellow', 'green', 'blue', 'red'] as const).map((c) => (
            <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
              onClick={() => createHighlight(c)} aria-label={c} title={c} />
          ))}
          <button className={styles.hiliteCancel} onClick={() => setPendingSel(null)} aria-label={t('Cancel')}>
            <X size={16} />
          </button>
        </div>
      )}

      {/* Progress */}
      <div className={styles.progressBar} aria-hidden="true">
        <div className={styles.progressFill} style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}
