import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'wouter';
import ePub from 'epubjs';
import {
  ChevronLeft, ChevronRight, X, List, Sun, Moon, Coffee, Loader2, Trash2,
  SlidersHorizontal,
} from 'lucide-react';
import {
  type ReaderSettings, useBook, useBookmark, useReaderSettings,
  useSaveBookmark, useSaveReaderSettings,
} from '../lib/queries';
import { apiPost, apiDelete, apiPatch, apiUrl, resourceUrl } from '../lib/api';
import { EmptyState } from '../components/EmptyState';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './Reader.module.css';

// Highlight colors as ARIA/label keys (SC 1.4.1: a color must never be conveyed
// by hue alone — every swatch + saved highlight carries the color's name).
const HILITE_ORDER = ['yellow', 'green', 'blue', 'red'] as const;
type HiliteColor = (typeof HILITE_ORDER)[number];

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

const FONT_MIN = 75;
const FONT_MAX = 200;
const LS_THEME = 'cwng.reader.theme';
const LS_FONT = 'cwng.reader.font';

const THEME_TO_READER: Record<ReaderSettings['theme'], ReaderTheme> = {
  lightTheme: 'light', sepiaTheme: 'sepia', darkTheme: 'dark', blackTheme: 'dark',
};
const READER_TO_THEME: Record<ReaderTheme, ReaderSettings['theme']> = {
  light: 'lightTheme', sepia: 'sepiaTheme', dark: 'darkTheme',
};
const FONT_FAMILY: Record<ReaderSettings['font'], string> = {
  default: '', Yahei: 'Microsoft YaHei, sans-serif', SimSun: 'SimSun, serif',
  KaiTi: 'KaiTi, serif', Arial: 'Arial, sans-serif',
};

function loadTheme(): ReaderTheme {
  const v = localStorage.getItem(LS_THEME);
  if (v === 'light' || v === 'sepia' || v === 'dark') return v;
  // First reader visit follows the already-resolved per-user app palette.
  // Thereafter the reader's explicit page-theme choice remains independent.
  const appTheme = document.documentElement.getAttribute('data-theme');
  if (appTheme === 'light') return 'light';
  if (appTheme === 'sepia') return 'sepia';
  return 'dark';
}
function loadFont(): number {
  const v = Number(localStorage.getItem(LS_FONT));
  return v >= FONT_MIN && v <= FONT_MAX ? v : 100;
}

export function Reader({ id }: { id: string }) {
  const t = useT();
  const announce = useAnnouncer();
  const { data: book, isLoading, error } = useBook(id);
  const { data: savedBookmark, isFetched: isBookmarkFetched } = useBookmark(id, 'epub');
  const { data: settingsData, isFetched: isSettingsFetched } = useReaderSettings();
  const saveBookmark = useSaveBookmark(id);
  const saveSettings = useSaveReaderSettings();

  const viewerRef = useRef<HTMLDivElement>(null);
  const tocRef = useRef<HTMLElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  // Edit/remove popover for an existing highlight (#782). Separate from popRef
  // (the create-color popover) so each has its own focus-trap lifecycle; the two
  // are mutually exclusive — opening one closes the other.
  const hlPopRef = useRef<HTMLDivElement>(null);
  const renditionRef = useRef<any>(null);
  const bookRef = useRef<any>(null);

  // Localized color names for highlight swatches + accessible labels.
  const colorLabel = (c: HiliteColor) =>
    ({ yellow: t('Yellow'), green: t('Green'), blue: t('Blue'), red: t('Red') })[c];
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settingsSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settingsPendingRef = useRef<Partial<ReaderSettings>>({});
  const lastCfiRef = useRef<string | null>(null);
  // Hold the freshest saved CFI so it survives re-renders without re-running the effect.
  const savedCfiRef = useRef<string | null>(null);

  const [rendered, setRendered] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [toc, setToc] = useState<TocItem[]>([]);
  const [tocOpen, setTocOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<ReaderTheme>(loadTheme);
  const [fontPct, setFontPct] = useState(loadFont);
  const [fontFamily, setFontFamily] = useState<ReaderSettings['font']>('default');
  const [margin, setMargin] = useState(16);
  const [lineHeight, setLineHeight] = useState(150);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [progress, setProgress] = useState(0);
  // Pending text selection awaiting a highlight-color choice.
  const [pendingSel, setPendingSel] = useState<{ cfiRange: string; text: string } | null>(null);
  // Existing highlight the user tapped — drives the edit/remove popover (#782).
  const [activeHl, setActiveHl] = useState<{ cfiRange: string; id: string; color: string } | null>(null);

  const epubFormat = book?.formats.find((f) => f.format.toLowerCase() === 'epub');

  // C10: the TOC drawer and highlight popovers are overlays — trap focus while
  // open, restore on close, Escape closes (hooks run unconditionally every render).
  useFocusTrap(tocRef, { onClose: () => setTocOpen(false), active: tocOpen });
  useFocusTrap(settingsRef, { onClose: () => setSettingsOpen(false), active: settingsOpen });
  useFocusTrap(popRef, { onClose: () => setPendingSel(null), active: !!pendingSel });
  useFocusTrap(hlPopRef, { onClose: () => setActiveHl(null), active: !!activeHl });

  // Open the edit/remove popover for a highlight the reader was tapped on (#782).
  // Closes the create-color popover so the two never show at once.
  const openHighlightEditor = useCallback((cfiRange: string, annotationId: string, color: string) => {
    setPendingSel(null);
    setActiveHl({ cfiRange, id: annotationId, color });
  }, []);

  // Paint a highlight onto the live rendition (epub.js annotations API). The
  // data param stashes the server annotation id + color so the click callback
  // knows which row it represents; a real click callback (3rd arg) + 'cwng-hl'
  // className (4th arg) make tapping the highlight open the editor (#782).
  const paintHighlight = useCallback((cfiRange: string, color: string, annotationId: string) => {
    try {
      renditionRef.current?.annotations?.highlight(
        cfiRange,
        { id: annotationId, color },
        () => openHighlightEditor(cfiRange, annotationId, color),
        'cwng-hl',
        { fill: HILITE_FILL[color] || HILITE_FILL.yellow, 'fill-opacity': '0.35' },
      );
    } catch { /* epub.js throws on a stale/foreign CFI — ignore */ }
  }, [openHighlightEditor]);

  // Create a highlight from the pending selection, persist it, paint it. The
  // create endpoint returns the new annotation row (incl. its id) — capture it
  // so the just-created highlight is immediately removable (#782).
  const createHighlight = useCallback(async (color: string) => {
    const sel = pendingSel;
    if (!sel) return;
    setPendingSel(null);
    try {
      const created = await apiPost<{ annotation_id?: string }>(`/annotations/${id}`, {
        cfi_range: sel.cfiRange, highlighted_text: sel.text, highlight_color: color,
      });
      paintHighlight(sel.cfiRange, color, created?.annotation_id ?? '');
    } catch { /* surfaced as no-op; user can retry */ }
    try {
      (renditionRef.current?.getContents?.() || []).forEach((c: any) => c.window?.getSelection?.().removeAllRanges());
    } catch { /* noop */ }
  }, [pendingSel, id, paintHighlight]);

  // Remove the tapped highlight server-side, then un-paint it (#782). Fails
  // silently (the reader has no toast) and leaves the highlight painted on
  // error — the row is still on the server, so keeping it painted stays honest.
  const removeHighlight = useCallback(async () => {
    const hl = activeHl;
    if (!hl) return;
    setActiveHl(null);
    try {
      await apiDelete(`/annotations/${id}/${hl.id}`);
      try { renditionRef.current?.annotations?.remove(hl.cfiRange, 'highlight'); } catch { /* noop */ }
    } catch { /* silent: keep the highlight painted */ }
  }, [activeHl, id]);

  // Recolor the tapped highlight (PATCH supports highlight_color). epub.js keys
  // an annotation by (cfiRange + type), so a new color is applied by removing
  // the old paint and re-adding with the new fill. Silent on error (old paint
  // is untouched, server keeps the prior color).
  const recolorHighlight = useCallback(async (color: string) => {
    const hl = activeHl;
    if (!hl) return;
    setActiveHl(null);
    if (hl.color === color) return;
    try {
      await apiPatch(`/annotations/${id}/${hl.id}`, { highlight_color: color });
      try { renditionRef.current?.annotations?.remove(hl.cfiRange, 'highlight'); } catch { /* noop */ }
      paintHighlight(hl.cfiRange, color, hl.id);
    } catch { /* silent: keep the highlight in its original color */ }
  }, [activeHl, id, paintHighlight]);

  useEffect(() => {
    savedCfiRef.current = savedBookmark?.bookmark ?? savedCfiRef.current;
  }, [savedBookmark]);

  useEffect(() => {
    const settings = settingsData?.reader;
    if (!settings) return;
    setTheme(THEME_TO_READER[settings.theme]);
    setFontPct(settings.fontSize);
    setFontFamily(settings.font);
    setMargin(settings.margin);
    setLineHeight(settings.lineHeight);
    // Start epub.js only on the next render, after this server snapshot has
    // become the state captured by the rendition callbacks.
    setSettingsHydrated(true);
  }, [settingsData]);

  const persistSetting = useCallback(<K extends keyof ReaderSettings>(key: K, value: ReaderSettings[K]) => {
    settingsPendingRef.current = { ...settingsPendingRef.current, [key]: value };
    if (settingsSaveTimer.current) clearTimeout(settingsSaveTimer.current);
    settingsSaveTimer.current = setTimeout(() => {
      const patch = settingsPendingRef.current;
      settingsPendingRef.current = {};
      settingsSaveTimer.current = null;
      saveSettings.mutate(patch, {
        onSuccess: () => announce(t('Reader settings saved.')),
        onError: () => announce(t('Could not save reader settings.'), { assertive: true }),
      });
    }, 300);
  }, [saveSettings, announce, t]);

  const persistCfi = useCallback(
    (cfi: string) => {
      lastCfiRef.current = cfi;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        saveTimer.current = null;
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

  const applyTypography = useCallback(() => {
    const rendition = renditionRef.current;
    if (!rendition) return;
    rendition.themes.fontSize(`${fontPct}%`);
    if (fontFamily === 'default') rendition.themes.font('initial');
    else rendition.themes.font(FONT_FAMILY[fontFamily]);
    try {
      (rendition.getContents?.() || []).forEach((c: any) => {
        if (!c?.document?.body) return;
        c.document.body.style.setProperty('padding-inline', `${margin}px`, 'important');
        c.document.body.style.setProperty('line-height', String(lineHeight / 100), 'important');
      });
    } catch { /* same-origin blob content; guard regardless */ }
  }, [fontPct, fontFamily, margin, lineHeight]);

  const goPrev = useCallback(() => renditionRef.current?.prev(), []);
  const goNext = useCallback(() => renditionRef.current?.next(), []);

  // Build the rendition once the epub format + its download URL are known.
  useEffect(() => {
    if (!epubFormat || !viewerRef.current || !isBookmarkFetched || !isSettingsFetched || !settingsHydrated) return;
    let cancelled = false;
    setRendered(false);
    setRenderError(null);

    (async () => {
      try {
        // Fetch the .epub ourselves (same-origin cookie auth) and hand epub.js
        // an ArrayBuffer — reliable archive open regardless of the URL extension.
        const res = await fetch(resourceUrl(epubFormat.download_url), { credentials: 'include' });
        if (!res.ok) throw new Error(t('Could not load the book file ({status})', { status: res.status }));
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

        // C10 (SC 4.1.2): epub.js renders each section into an <iframe> with no
        // title — screen readers announce "frame" with no name. Title them as
        // they render so the book content region is named.
        rendition.on('rendered', () => {
          viewerRef.current?.querySelectorAll('iframe').forEach((f) => {
            f.setAttribute('title', t('Book content'));
          });
          applyTheme(theme);
          applyTypography();
        });

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

        // Render existing highlights (the CFI-anchored ones we can place). Each
        // row carries its server annotation_id; paintHighlight stashes it in the
        // epub.js data param so a later tap can target the right row (#782).
        fetch(apiUrl(`/annotations/${id}/data.json`), { credentials: 'include' })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (cancelled || !d) return;
            (d.annotations || []).forEach((a: any) => {
              if (a.cfi_range) {
                paintHighlight(a.cfi_range, a.highlight_color || 'yellow', a.annotation_id);
              }
            });
          })
          .catch(() => { /* highlights are best-effort */ });

        // Capture a text selection → offer a highlight-color popover.
        rendition.on('selected', (cfiRange: string, contents: any) => {
          let text = '';
          try { text = (contents?.window?.getSelection?.().toString() || '').trim(); } catch { /* noop */ }
          if (cfiRange) {
            setActiveHl(null);
            setPendingSel({ cfiRange, text });
          }
        });
      } catch (e) {
        if (!cancelled) setRenderError(e instanceof Error ? e.message : t('Failed to open the book.'));
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
        const cfi = lastCfiRef.current;
        if (cfi) {
          void apiPost(`/api/v1/books/${id}/bookmark`, { format: 'epub', bookmark: cfi }, { keepalive: true });
        }
      }
      try { renditionRef.current?.destroy(); } catch { /* noop */ }
      try { bookRef.current?.destroy(); } catch { /* noop */ }
      renditionRef.current = null;
      bookRef.current = null;
    };
    // Re-render only when the source changes; theme/font are applied imperatively.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epubFormat?.download_url, isBookmarkFetched, isSettingsFetched, settingsHydrated]);

  // Apply theme / font changes to a live rendition without rebuilding it, and
  // remember the preference across sessions.
  useEffect(() => {
    localStorage.setItem(LS_THEME, theme);
    applyTheme(theme);
  }, [theme, applyTheme]);
  useEffect(() => {
    localStorage.setItem(LS_FONT, String(fontPct));
    applyTypography();
  }, [fontPct, fontFamily, margin, lineHeight, applyTypography]);

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
        {/* Page heading for the reader view (SC 1.3.1), visually the bar title. */}
        <VisuallyHidden as="h1">{book.title}</VisuallyHidden>
        <Link href={`/book/${id}`} className={styles.iconBtn} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={20} aria-hidden="true" focusable={false} />
        </Link>
        <span className={styles.bookTitle} aria-hidden="true">{book.title}</span>
        <div className={styles.barControls}>
          <button className={styles.iconBtn} onClick={() => setTocOpen((o) => !o)}
            aria-label={t('Table of contents')} aria-expanded={tocOpen} title={t('Contents')}>
            <List size={19} aria-hidden="true" focusable={false} />
          </button>
          <button className={styles.iconBtn} onClick={() => setSettingsOpen((o) => !o)}
            aria-label={t('Reading appearance')} aria-expanded={settingsOpen} title={t('Reading appearance')}>
            <SlidersHorizontal size={19} aria-hidden="true" focusable={false} />
          </button>
        </div>
      </header>

      {/* TOC drawer */}
      {tocOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setTocOpen(false)} aria-hidden="true" />
          <nav ref={tocRef} className={styles.toc} aria-label={t('Table of contents')} tabIndex={-1}>
            <div className={styles.panelHeading}>
              <p className={styles.tocHeading}>{t('Contents')}</p>
              <button className={styles.iconBtn} onClick={() => setTocOpen(false)} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            {toc.length === 0 ? (
              <p className={styles.tocEmpty}>{t('No contents found.')}</p>
            ) : (
              <ul role="list">
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

      {settingsOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setSettingsOpen(false)} aria-hidden="true" />
          <div ref={settingsRef} className={styles.settingsPanel} role="dialog" aria-modal="true"
            aria-labelledby="reader-appearance-title" tabIndex={-1}>
            <div className={styles.panelHeading}>
              <h2 id="reader-appearance-title">{t('Reading appearance')}</h2>
              <button className={styles.iconBtn} onClick={() => setSettingsOpen(false)} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            <fieldset className={styles.settingGroup}>
              <legend>{t('Page theme')}</legend>
              <div className={styles.themeChoices}>
                {([
                  ['light', Sun, t('Light')], ['sepia', Coffee, t('Sepia')], ['dark', Moon, t('Dark')],
                ] as const).map(([value, Icon, label]) => (
                  <button key={value} className={theme === value ? styles.choiceActive : styles.choice}
                    aria-pressed={theme === value} onClick={() => {
                      setTheme(value); persistSetting('theme', READER_TO_THEME[value]);
                    }}>
                    <Icon size={17} aria-hidden="true" focusable={false} /> {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className={styles.settingField}>
              <span>{t('Font family')}</span>
              <select value={fontFamily} onChange={(e) => {
                const value = e.target.value as ReaderSettings['font'];
                setFontFamily(value); persistSetting('font', value);
              }}>
                <option value="default">{t('Book default')}</option>
                <option value="Arial">Arial</option><option value="Yahei">Microsoft YaHei</option>
                <option value="SimSun">SimSun</option><option value="KaiTi">KaiTi</option>
              </select>
            </label>
            {([
              ['font-size', t('Font size'), fontPct, FONT_MIN, FONT_MAX, '%', setFontPct, 'fontSize'],
              ['page-margin', t('Page margins'), margin, 0, 80, 'px', setMargin, 'margin'],
              ['line-height', t('Line height'), lineHeight, 100, 220, '%', setLineHeight, 'lineHeight'],
            ] as const).map(([key, label, value, min, max, unit, setter, settingKey]) => (
              <label key={key} className={styles.settingField} htmlFor={`reader-${key}`}>
                <span>{label} <output>{value}{unit}</output></span>
                <input id={`reader-${key}`} type="range" min={min} max={max}
                  step={key === 'page-margin' ? 4 : key === 'font-size' ? 5 : 10}
                  value={value} onChange={(e) => {
                    const next = Number(e.target.value);
                    setter(next);
                    persistSetting(settingKey, next as never);
                  }} />
              </label>
            ))}
          </div>
        </>
      )}

      {/* Viewer + page-turn zones */}
      <div className={styles.stage}>
        <button className={`${styles.navZone} ${styles.navPrev}`} onClick={goPrev} aria-label={t('Previous page')}>
          <ChevronLeft size={28} aria-hidden="true" focusable={false} />
        </button>
        <div ref={viewerRef} className={styles.viewer} />
        <button className={`${styles.navZone} ${styles.navNext}`} onClick={goNext} aria-label={t('Next page')}>
          <ChevronRight size={28} aria-hidden="true" focusable={false} />
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
        <div ref={popRef} className={styles.hilitePop} role="dialog" aria-modal="true"
          aria-label={t('Highlight color')} tabIndex={-1}>
          <span className={styles.hiliteLabel}>{t('Highlight')}</span>
          {HILITE_ORDER.map((c) => (
            <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
              onClick={() => createHighlight(c)} aria-label={colorLabel(c)} title={colorLabel(c)} />
          ))}
          <button className={styles.hiliteCancel} onClick={() => setPendingSel(null)} aria-label={t('Cancel')}>
            <X size={16} aria-hidden="true" focusable={false} />
          </button>
        </div>
      )}

      {/* Edit/remove popover for a tapped existing highlight (#782).
          Swatches recolor (PATCH); the Remove button deletes (DELETE) + unpaints. */}
      {activeHl && (
        <div ref={hlPopRef} className={styles.hilitePop} role="dialog" aria-modal="true"
          aria-label={t('Highlight color')} tabIndex={-1}>
          <span className={styles.hiliteLabel}>{t('Highlight')}</span>
          {HILITE_ORDER.map((c) => (
            <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
              onClick={() => recolorHighlight(c)}
              aria-pressed={activeHl.color === c} aria-label={colorLabel(c)} title={colorLabel(c)} />
          ))}
          <button className={styles.hiliteRemove} onClick={removeHighlight} title={t('Remove highlight')}>
            <Trash2 size={15} aria-hidden="true" focusable={false} />
            <span>{t('Remove highlight')}</span>
          </button>
          <button className={styles.hiliteCancel} onClick={() => setActiveHl(null)} aria-label={t('Cancel')}>
            <X size={16} aria-hidden="true" focusable={false} />
          </button>
        </div>
      )}

      {/* Progress (SC 4.1.2: a named progressbar, not an aria-hidden bar). */}
      <div className={styles.progressBar} role="progressbar"
        aria-label={t('Reading progress')}
        aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}
        aria-valuetext={t('{pct}% read', { pct: Math.round(progress) })}>
        <div className={styles.progressFill} style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}
